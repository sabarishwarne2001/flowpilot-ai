"""ARCH-40 — the unified review hub.

    GET  /workspaces/{wid}/review                        queue    [CONTRIBUTOR]
    GET  /workspaces/{wid}/review/assignees              people   [CONTRIBUTOR]
    POST /workspaces/{wid}/review/bulk                   bulk     [CONTRIBUTOR]
    POST /workspaces/{wid}/review/{kind}/{id}/resolve    resolve  [CONTRIBUTOR]
    POST /workspaces/{wid}/review/{kind}/{id}/assign     assign   [CONTRIBUTOR]
    DEL  /workspaces/{wid}/review/{kind}/{id}/assign     unassign [CONTRIBUTOR]

ROUTE ORDERING
==============

Mounted on its own `/review` prefix, which is the whole reason it is not
hanging off `/work-items`. FastAPI matches in registration order, and
`work_items.router` carries `GET /{work_item_id}`: any literal segment added
to that prefix has to be registered ahead of it, which is why ARCH-38's
`ingestion.work_item_router` is mounted first and why gate B4 and mutant M8
pin that ordering. A separate prefix has no catch-all to lose a race with.

Within this router the only parameterised paths are three segments deep
(`/{kind}/{item_id}/…`), so `/bulk` and `/assignees` cannot be captured by
them at any registration order. Nothing here is order-dependent.

AUTHORISATION
=============

CONTRIBUTOR, matching the three source endpoints this dispatches to — the hub
must not be a way to do something the source screen would have refused.
Workspace scope comes from `deps.RequireWorkspaceContributor`, and every read
and write then goes through `projection`, which applies the workspace
predicate again against the view. Two independent checks, because the hub is
the one surface that touches all three tenanted tables at once.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import deps
from app.core.exceptions import FlowPilotError
from app.schemas.review import (
    ReviewAssignRequest,
    ReviewAssigneeResponse,
    ReviewBulkItemResult,
    ReviewBulkRequest,
    ReviewBulkResponse,
    ReviewItemResponse,
    ReviewQueueResponse,
    ReviewResolveRequest,
    ReviewResolveResponse,
)
from app.services.review import projection, resolution
from app.services.review import vocabulary as vocab

logger = logging.getLogger("app.api.v1.review")

router = APIRouter(tags=["Review Hub"])


class ReviewConflictError(FlowPilotError):
    """ARCH48-S1:review-conflict. A 409 in the ARCH-01 envelope, `{code, message, details}`.

    One subclass per refusal code (the domain handler reads `code` from the
    class): STALE_VERSION (details.current_version), LOCKED (details.holder,
    details.expires_at), ALREADY_RESOLVED. The console branches on the code;
    `detail` still carries the message for callers that read only that.
    """

    status_code = 409
    code = "REVIEW_CONFLICT"

    def __init__(self, message: str, details: Optional[dict] = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


_CONFLICTS: dict[str, type[ReviewConflictError]] = {}


def _conflict(exc: "resolution.ReviewResolutionError") -> ReviewConflictError:
    klass = _CONFLICTS.get(exc.code)
    if klass is None:
        klass = type(f"ReviewConflict_{exc.code}", (ReviewConflictError,), {"code": exc.code})
        _CONFLICTS[exc.code] = klass
    return klass(str(exc), {"code": exc.code, **getattr(exc, "details", {})})


_TURN_ERRORS = (resolution.StaleVersionError, resolution.ItemLockedError, resolution.AlreadyResolvedError)


def decision_guard(db: Session, context: deps.TenantContext, *, kind: str, item_id: uuid.UUID) -> None:
    """ARCH48-S1:decision-guard. For a source screen deciding a hub item itself (anomalies,
    tables, comparisons, obligations, ERP postings, packet splits): wait this item's turn, and
    refuse with the hub's 409 when someone else decided it meanwhile or holds its lock."""
    try:
        resolution.guard_decision(db, workspace_id=context.workspace_id, kind=kind, item_id=item_id,
                                  actor_user_id=context.user_id)
    except _TURN_ERRORS as exc:
        db.rollback()
        raise _conflict(exc) from exc


def _allowed_kinds(db: Session, context: deps.TenantContext) -> tuple[str, ...]:
    """ARCH40-S1:hub-capability-gate. The kinds this organization may see.

    The source endpoints gate clause assertions on
    capability.semantic_assertions (reads included — verify_arch33 insists)
    and anomaly findings on capability.anomaly_radar. A hub that ignored
    those would let an organization that lost a capability on a downgrade go
    on reading and resolving exactly what the source screen now refuses.
    Extraction review is core and always allowed.
    """
    from app.api import capability_gate
    from app.core.entitlements import (
        ANOMALY_RADAR_CAPABILITY,
        SEMANTIC_ASSERTIONS_CAPABILITY,
    )

    granted = set(
        capability_gate.granted_capabilities(db, organization_id=context.organization_id)
    )
    kinds = [vocab.KIND_EXTRACTION]
    if SEMANTIC_ASSERTIONS_CAPABILITY in granted:
        kinds.append(vocab.KIND_ASSERTION)
    if ANOMALY_RADAR_CAPABILITY in granted:
        kinds.append(vocab.KIND_ANOMALY)
    # ARCH42-S1:hub-merge-gate. Merge proposals are the entity graph's, and
    # every entity route is gated on capability.entity_graph.
    from app.core.entitlements import ENTITY_GRAPH_CAPABILITY

    if ENTITY_GRAPH_CAPABILITY in granted:
        kinds.append(vocab.KIND_MERGE)
    # ARCH43-S1:hub-split-gate. Split plans are the packet dicer's, and every
    # packet route is gated on capability.case_intelligence.
    from app.core.entitlements import CASE_INTELLIGENCE_CAPABILITY

    if CASE_INTELLIGENCE_CAPABILITY in granted:
        kinds.append(vocab.KIND_SPLIT)
    # ARCH44-S1:hub-table-gate. Flagged tables are the table extractor's, and
    # every table route is gated on capability.table_intelligence.
    from app.core.entitlements import TABLE_INTELLIGENCE_CAPABILITY

    if TABLE_INTELLIGENCE_CAPABILITY in granted:
        kinds.append(vocab.KIND_TABLE)
    # ARCH45-S1:hub-corroboration-gate. Comparisons are the corroborator's, and
    # every corroboration route is gated on capability.universal_corroborator.
    from app.core.entitlements import UNIVERSAL_CORROBORATOR_CAPABILITY

    if UNIVERSAL_CORROBORATOR_CAPABILITY in granted:
        kinds.append(vocab.KIND_CORROBORATION)
    # ARCH46-S1:hub-obligation-gate. Obligations are capability.obligations's,
    # and every obligation route is gated on it.
    from app.core.entitlements import OBLIGATIONS_CAPABILITY

    if OBLIGATIONS_CAPABILITY in granted:
        kinds.append(vocab.KIND_OBLIGATION)
    # ARCH47-S1:hub-posting-gate. Postings are capability.erp_posting's, and every
    # ERP route is gated on it.
    from app.core.entitlements import ERP_POSTING_CAPABILITY

    if ERP_POSTING_CAPABILITY in granted:
        kinds.append(vocab.KIND_POSTING)
    return tuple(kinds)


def _require_kind(db: Session, context: deps.TenantContext, kind: str) -> None:
    """400 for a kind that does not exist, 403 for one the plan excludes."""
    if kind not in vocab.KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{kind}' is not a review kind.",
        )
    if kind not in _allowed_kinds(db, context):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Your plan does not include {kind.lower()} review, so these "
                "items cannot be opened or resolved here."
            ),
        )


def _payload(body: Optional[ReviewResolveRequest]) -> resolution.ResolvePayload:
    if body is None:
        return resolution.ResolvePayload()
    return resolution.ResolvePayload(
        values=body.values,
        reviewer_verdict=body.reviewer_verdict,
        corrected_quote=body.corrected_quote,
        corrected_value=body.corrected_value,
        anomaly_verdict=body.anomaly_verdict,
        note=body.note,
        ttl_days=body.ttl_days,
        merge_verdict=body.merge_verdict,  # ARCH42-S1:merge-verdict
        split_verdict=body.split_verdict,  # ARCH43-S1:split-verdict
        split_boundaries=body.split_boundaries,
        table_verdict=body.table_verdict,  # ARCH44-S1:table-verdict
        corroboration_verdict=body.corroboration_verdict,  # ARCH45-S1:corroboration-verdict
        obligation_verdict=body.obligation_verdict,  # ARCH46-S1:obligation-verdict
        posting_verdict=body.posting_verdict,  # ARCH47-S1:posting-verdict
        posting_reference=body.posting_reference,
    )


def _as_response(item: projection.ReviewItem) -> ReviewItemResponse:
    return ReviewItemResponse(
        kind=item.kind,
        item_id=item.item_id,
        work_item_id=item.work_item_id,
        document_name=item.document_name,
        headline=item.headline,
        severity=item.severity,
        confidence=float(item.confidence) if item.confidence is not None else None,
        created_at=item.created_at,
        age_seconds=item.age_seconds,
        status=item.status,
        assignee_user_id=item.assignee_user_id,
        assignee_email=item.assignee_email,
        under_retention_hold=item.under_retention_hold,
        tags=item.tags,
        review_reason=item.review_reason,
        version=item.version,
        open_threads=item.open_threads,
    )


@router.get(
    "",
    response_model=ReviewQueueResponse,
    summary="The unified review queue",
    response_description=(
        "One page across extraction reviews, clause assertions and anomaly "
        "findings, ordered by severity then age."
    ),
)
def list_reviews(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
    kind: Annotated[Optional[list[str]], Query()] = None,
    severity: Annotated[Optional[list[str]], Query()] = None,
    review_status: str = Query(default=vocab.STATUS_OPEN, alias="status"),
    reason: Annotated[Optional[list[str]], Query()] = None,
    work_item_id: Optional[uuid.UUID] = None,
    tag: Optional[str] = None,
    assignee_user_id: Optional[uuid.UUID] = None,
    unassigned_only: bool = False,
    min_age_seconds: Optional[int] = Query(default=None, ge=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=vocab.DEFAULT_PAGE_SIZE, ge=1, le=vocab.MAX_PAGE_SIZE),
) -> ReviewQueueResponse:
    allowed = _allowed_kinds(db, context)
    requested = list(kind or allowed)
    unknown = [k for k in requested if k not in vocab.KINDS]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{unknown[0]}' is not a review kind.",
        )
    visible = [k for k in requested if k in allowed]
    if not visible:
        return ReviewQueueResponse(
            items=[], total=0, page=page, page_size=page_size,
            counts_by_kind={k: 0 for k in vocab.KINDS},
            allowed_kinds=list(allowed),
        )

    try:
        result = projection.query_reviews(
            db,
            workspace_id=context.workspace_id,
            kinds=visible,
            severities=severity,
            status=review_status,
            reasons=reason,
            work_item_id=work_item_id,
            tag=tag,
            assignee_user_id=assignee_user_id,
            unassigned_only=unassigned_only,
            min_age_seconds=min_age_seconds,
            page=page,
            page_size=page_size,
        )
    except projection.ReviewQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return ReviewQueueResponse(
        items=[_as_response(item) for item in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        counts_by_kind=result.counts_by_kind,
        allowed_kinds=list(allowed),
    )


@router.get(
    "/assignees",
    response_model=list[ReviewAssigneeResponse],
    summary="Workspace members who can hold review items",
)
def list_assignees(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> list[ReviewAssigneeResponse]:
    from app.models.review import ReviewAssignment
    from app.models.user import User
    from app.models.workspace import WorkspaceMember

    counts = (
        select(
            ReviewAssignment.assignee_user_id.label("user_id"),
            func.count().label("open_items"),
        )
        .where(ReviewAssignment.workspace_id == context.workspace_id)
        .group_by(ReviewAssignment.assignee_user_id)
        .subquery()
    )

    rows = db.execute(
        select(User.id, User.email, func.coalesce(counts.c.open_items, 0))
        .select_from(WorkspaceMember)
        .join(User, User.id == WorkspaceMember.user_id)
        .outerjoin(counts, counts.c.user_id == User.id)
        .where(WorkspaceMember.workspace_id == context.workspace_id)
        .order_by(User.email.asc())
    ).all()

    return [
        ReviewAssigneeResponse(user_id=row[0], email=str(row[1]), open_items=int(row[2]))
        for row in rows
    ]


@router.post(
    "/bulk",
    response_model=ReviewBulkResponse,
    summary="Resolve, assign or unassign many items",
    response_description=(
        "ARCH-38's per-item result contract: every id comes back with an "
        "outcome, and one refusal does not abort the rest."
    ),
)
def bulk(
    body: ReviewBulkRequest,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> ReviewBulkResponse:
    _require_kind(db, context, body.kind)
    results: list[ReviewBulkItemResult] = []
    payload = _payload(body.payload)

    # Deduplicated, order preserved. A caller who sends the same id twice gets
    # one result for it rather than a second attempt that refuses because the
    # first one succeeded.
    seen: set[uuid.UUID] = set()
    ordered_ids = [i for i in body.ids if not (i in seen or seen.add(i))]

    expected_versions = body.expected_versions or {}

    def refused(item_id: uuid.UUID, work_item_id: object, code: str, detail: Optional[str]) -> ReviewBulkItemResult:
        return ReviewBulkItemResult(work_item_id=str(work_item_id or item_id), review_item_id=str(item_id),
                                    outcome="refused", code=code, detail=detail)

    def skipped(item_id: uuid.UUID, work_item_id: object, code: str) -> ReviewBulkItemResult:
        return ReviewBulkItemResult(work_item_id=str(work_item_id or item_id), review_item_id=str(item_id),
                                    outcome="skipped", code=code)

    def decide(item_id: uuid.UUID) -> ReviewBulkItemResult:
        item = projection.load_item(
            db, workspace_id=context.workspace_id, kind=body.kind, item_id=item_id
        )
        if item is None:
            return refused(item_id, None, "NOT_FOUND", "No such review item in this workspace.")

        # ARCH48-S1:bulk-savepoint. Each item in its own SAVEPOINT: a refusal
        # (a stale version, someone's lock, a payload the kind rejects) rolls
        # back that item alone. Before ARCH-48 the refusal path called
        # `db.rollback()`, which discarded every earlier item of the batch
        # while the response still reported them "ok".
        savepoint = db.begin_nested()
        try:
            if body.action == "resolve":
                if item.status == vocab.STATUS_RESOLVED:
                    savepoint.commit()
                    return skipped(item_id, item.work_item_id, "ALREADY_RESOLVED")
                outcome = resolution.resolve_item(
                    db, item=item, actor_user_id=context.user_id, payload=payload,
                    expected_version=expected_versions.get(item_id),
                )
                detail = outcome.detail
            elif body.action == "assign":
                if body.assignee_user_id is None:
                    raise resolution.ReviewResolutionError("assign needs assignee_user_id.")
                resolution.assign(
                    db,
                    workspace_id=context.workspace_id,
                    kind=body.kind,
                    item_id=item_id,
                    assignee_user_id=body.assignee_user_id,
                    assigned_by_user_id=context.user_id,
                )
                detail = "assigned"
            else:
                changed = resolution.clear_assignment(
                    db, workspace_id=context.workspace_id, kind=body.kind, item_id=item_id
                )
                if not changed:
                    savepoint.commit()
                    return skipped(item_id, item.work_item_id, "NOT_ASSIGNED")
                detail = "unassigned"
        except resolution.ReviewResolutionError as exc:
            if savepoint.is_active:
                savepoint.rollback()
            return refused(item_id, item.work_item_id, exc.code, str(exc))
        except BaseException:
            if savepoint.is_active:
                savepoint.rollback()
            raise
        savepoint.commit()
        return ReviewBulkItemResult(
            work_item_id=str(item.work_item_id or item_id),
            review_item_id=str(item_id),
            outcome="ok",
            detail=detail,
        )

    # ARCH48-S1:bulk-lock-order. Items are decided in ONE canonical order (by id)
    # and reported in the order asked. Each decision holds its item's version-row
    # lock until the commit, so two bulk requests over overlapping items taking
    # those locks in different orders could deadlock; in one order they queue.
    by_id = {item_id: decide(item_id) for item_id in sorted(ordered_ids, key=str)}

    db.commit()
    results = [by_id[item_id] for item_id in ordered_ids]

    return ReviewBulkResponse(
        action=body.action,
        kind=body.kind,
        results=results,
        ok=sum(1 for r in results if r.outcome == "ok"),
        refused=sum(1 for r in results if r.outcome == "refused"),
        skipped=sum(1 for r in results if r.outcome == "skipped"),
    )


@router.post(
    "/{kind}/{item_id}/resolve",
    response_model=ReviewResolveResponse,
    summary="Resolve one item through its owning service",
)
def resolve_one(
    kind: str,
    item_id: uuid.UUID,
    body: ReviewResolveRequest,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> ReviewResolveResponse:
    _require_kind(db, context, kind)

    try:
        outcome = resolution.resolve_scoped(
            db,
            workspace_id=context.workspace_id,
            kind=kind,
            item_id=item_id,
            actor_user_id=context.user_id,
            payload=_payload(body),
            expected_version=body.expected_version,  # ARCH48-S1:resolve-expected-version
        )
    except LookupError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except _TURN_ERRORS as exc:
        db.rollback()
        raise _conflict(exc) from exc
    except resolution.ReviewPermissionError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    except resolution.ReviewResolutionError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    db.commit()
    return ReviewResolveResponse(
        kind=outcome.kind,
        item_id=outcome.item_id,
        work_item_id=outcome.work_item_id,
        resolution=outcome.detail,
        version=outcome.version,
    )


@router.post(
    "/{kind}/{item_id}/assign",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Give an item to a workspace member",
)
def assign_one(
    kind: str,
    item_id: uuid.UUID,
    body: ReviewAssignRequest,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> Response:
    _require_kind(db, context, kind)
    try:
        resolution.assign(
            db,
            workspace_id=context.workspace_id,
            kind=kind,
            item_id=item_id,
            assignee_user_id=body.assignee_user_id,
            assigned_by_user_id=context.user_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except resolution.ReviewResolutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{kind}/{item_id}/assign",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Take an item back off a member",
)
def unassign_one(
    kind: str,
    item_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> Response:
    _require_kind(db, context, kind)
    resolution.clear_assignment(
        db, workspace_id=context.workspace_id, kind=kind, item_id=item_id
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
