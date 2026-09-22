"""ARCH-33 §4.5/§4.6 — the assertion endpoints.

    POST /workspaces/{wid}/assertions/preview                 compile   [CONTRIBUTOR]
    GET  /workspaces/{wid}/assertions/rules/{rid}             list      [VIEWER]
    PUT  /workspaces/{wid}/assertions/rules/{rid}/nodes/{key} save      [ADMIN]
    POST /workspaces/{wid}/assertions/simulate                test      [CONTRIBUTOR]
    GET  /workspaces/{wid}/assertions/reviews                 queue     [VIEWER]
    POST /workspaces/{wid}/assertions/reviews/{eid}/resolve   resolve   [CONTRIBUTOR]
    GET  /workspaces/{wid}/assertions/phrases                 learned   [ADMIN]

WHY SAVING IS ADMIN AND RESOLVING IS CONTRIBUTOR
================================================

The same split ARCH-31 made, for the same reason. Resolving one triaged
assertion decides one document; that is the daily work of the person who reads
contracts, and putting it behind ADMIN means the person who does the job
cannot do the job.

Saving an assertion changes what passes automatically for every future
document in the workspace. A setting that silently widens what gets waved
through is an administrative decision, and it is the setting an attacker with
a reviewer's credentials would reach for first.

`preview` and `simulate` are CONTRIBUTOR rather than ADMIN: both write
nothing, and a reviewer who wants to understand why a rule triaged their
document should be able to run it against another one.

EVERY ROUTE IS CAPABILITY-GATED, INCLUDING THE READS
====================================================

`require_capability` runs on the queue and the list as well as the writes.
Gating only the writes would let a tenant without the capability read every
clause finding the engine produced and simply not resolve them, which is the
product.

The gate raises `CapabilityRequiredError` -> 402 with the ARCH-01 envelope and
`remedy: PLAN_UPGRADE`. It is not caught here:
`app/core/exception_handlers.py` renders it, so the shape is identical to
every other domain refusal and the console's `ApiError` handling needs no new
branch.

WHY RESOLVING ENQUEUES RATHER THAN RESUMING INLINE
==================================================

§4.5: "the execution resumes on the edge matching the reviewer's verdict."
Resuming means re-walking a graph, which is `automation.execute`'s job and is
already idempotent on the outbox event id. Doing it inline here would be a
second code path to the same outcome, and the two would drift — which is the
defect ARCH-13's own verification resolver already avoids the same way.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.models.assertion import (
    AssertionDefinition,
    AssertionEvaluation,
    AssertionRetrievalPhrase,
)
from app.models.automation import AutomationRule
from app.models.work_item import WorkItem
from app.models.workspace import WorkspaceRole
from app.schemas.assertion import (
    AssertionDefinitionResponse,
    AssertionPreviewRequest,
    AssertionPreviewResponse,
    AssertionResolveRequest,
    AssertionReviewItemResponse,
    AssertionSaveRequest,
    AssertionSimulateRequest,
    AssertionSimulateResponse,
    RetrievalPhraseResponse,
)
from app.services.assertions import definition_service, triage as triage_service
from app.services.assertions import vocabulary as vocab

logger = logging.getLogger("app.api.v1.assertions")

router = APIRouter(tags=["Clause Assertions"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
RequireAdmin = RequireWorkspaceRole(WorkspaceRole.ADMIN)

CAPABILITY = entitlements.SEMANTIC_ASSERTIONS_CAPABILITY


def _gate(db: Session, context: TenantContext, operation: str) -> None:
    capability_gate.require_capability(
        db, context=context, capability_key=CAPABILITY, operation=operation
    )


def _assert_workspace(context: TenantContext, workspace_id: uuid.UUID) -> None:
    """ARCH-02. The path id must be the resolved context's workspace."""
    if context.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found."
        )


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
    )


def _definition_payload(
    definition: AssertionDefinition,
) -> AssertionDefinitionResponse:
    plan = definition_service.plan_from_row(definition)
    return AssertionDefinitionResponse(
        id=definition.id,
        node_id=definition.node_id,
        sentence=definition.sentence,
        family=definition.family,
        evaluation_mode=definition.evaluation_mode,
        threshold=definition.threshold,
        version=definition.version,
        plan=dict(definition.plan or {}),
        understood_as=plan.describe(),
        llm_acknowledged_by=definition.llm_acknowledged_by,
        created_at=definition.created_at,
    )


# ---------------------------------------------------------------------------
# Rule builder
# ---------------------------------------------------------------------------


@router.post(
    "/workspaces/{workspace_id}/assertions/preview",
    response_model=AssertionPreviewResponse,
)
def preview_assertion(
    workspace_id: uuid.UUID,
    payload: AssertionPreviewRequest,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> AssertionPreviewResponse:
    """Compile a sentence and describe the consequence. Writes nothing."""
    _assert_workspace(context, workspace_id)
    _gate(db, context, "assertion.preview")

    try:
        compiled = definition_service.preview(
            db,
            organization_id=context.organization_id,
            sentence=payload.sentence,
            threshold=payload.threshold or vocab.DEFAULT_THRESHOLD,
        )
    except definition_service.AssertionError_ as exc:
        raise _bad_request(exc) from exc

    return AssertionPreviewResponse(**compiled.as_payload())


@router.get(
    "/workspaces/{workspace_id}/assertions/rules/{rule_id}",
    response_model=list[AssertionDefinitionResponse],
)
def list_rule_assertions(
    workspace_id: uuid.UUID,
    rule_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> list[AssertionDefinitionResponse]:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "assertion.list")

    rule = db.execute(
        select(AutomationRule).where(
            AutomationRule.id == rule_id,
            AutomationRule.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found."
        )

    return [
        _definition_payload(definition)
        for definition in definition_service.latest_for_rule(db, rule_id=rule.id)
    ]


@router.put(
    "/workspaces/{workspace_id}/assertions/rules/{rule_id}/nodes/{node_key}",
    response_model=AssertionDefinitionResponse,
    status_code=status.HTTP_200_OK,
)
def save_assertion(
    workspace_id: uuid.UUID,
    rule_id: uuid.UUID,
    node_key: str,
    payload: AssertionSaveRequest,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireAdmin),
) -> AssertionDefinitionResponse:
    """Compile and version an assertion onto one node of one rule."""
    _assert_workspace(context, workspace_id)
    _gate(db, context, "assertion.save")

    rule = db.execute(
        select(AutomationRule).where(
            AutomationRule.id == rule_id,
            AutomationRule.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found."
        )

    if payload.node_key != node_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The node key in the path and the body must match.",
        )

    try:
        node = definition_service.assertion_node_for(
            db, rule=rule, node_key=node_key
        )
        definition = definition_service.save(
            db,
            organization_id=context.organization_id,
            workspace_id=workspace_id,
            node=node,
            sentence=payload.sentence,
            threshold=payload.threshold,
            acknowledged_by=(
                context.user_id if payload.acknowledge_llm else None
            ),
        )
    except definition_service.AssertionError_ as exc:
        raise _bad_request(exc) from exc

    db.commit()
    db.refresh(definition)
    return _definition_payload(definition)


@router.post(
    "/workspaces/{workspace_id}/assertions/simulate",
    response_model=AssertionSimulateResponse,
)
def simulate_assertion(
    workspace_id: uuid.UUID,
    payload: AssertionSimulateRequest,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> AssertionSimulateResponse:
    """§4.6's "Test on a document". Runs the real path; starts no execution."""
    _assert_workspace(context, workspace_id)
    _gate(db, context, "assertion.simulate")

    work_item = db.execute(
        select(WorkItem).where(
            WorkItem.id == payload.work_item_id,
            WorkItem.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if work_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found."
        )

    definition: Optional[AssertionDefinition] = None
    if payload.definition_id is not None:
        definition = db.execute(
            select(AssertionDefinition).where(
                AssertionDefinition.id == payload.definition_id,
                AssertionDefinition.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()
        if definition is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assertion not found.",
            )
    elif not (payload.sentence or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Send either a saved assertion or a sentence to test.",
        )

    try:
        result = definition_service.simulate(
            db,
            definition=definition,
            sentence=payload.sentence,
            threshold=payload.threshold or vocab.DEFAULT_THRESHOLD,
            organization_id=context.organization_id,
            workspace_id=workspace_id,
            work_item=work_item,
        )
    except definition_service.AssertionError_ as exc:
        raise _bad_request(exc) from exc

    # A simulation writes nothing of its own, but retrieval and the LLM route
    # may have flushed session state. Roll back rather than commit, so a
    # preview can never leave a row behind.
    db.rollback()
    return AssertionSimulateResponse(**result.as_payload())


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


@router.get(
    "/workspaces/{workspace_id}/assertions/reviews",
    response_model=list[AssertionReviewItemResponse],
)
def list_review_queue(
    workspace_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    work_item_id: Optional[uuid.UUID] = Query(default=None),
) -> list[AssertionReviewItemResponse]:
    """Triaged assertions still waiting for a human."""
    _assert_workspace(context, workspace_id)
    _gate(db, context, "assertion.review_list")

    statement = (
        select(AssertionEvaluation, AssertionDefinition, WorkItem)
        .join(
            AssertionDefinition,
            AssertionDefinition.id == AssertionEvaluation.definition_id,
        )
        .join(WorkItem, WorkItem.id == AssertionEvaluation.work_item_id)
        .where(
            AssertionEvaluation.workspace_id == workspace_id,
            AssertionEvaluation.routed_to == vocab.ROUTE_TRIAGE,
            AssertionEvaluation.reviewer_verdict.is_(None),
        )
        .order_by(AssertionEvaluation.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if work_item_id is not None:
        statement = statement.where(AssertionEvaluation.work_item_id == work_item_id)

    items: list[AssertionReviewItemResponse] = []
    for evaluation, definition, work_item in db.execute(statement).all():
        plan = definition_service.plan_from_row(definition)
        items.append(
            AssertionReviewItemResponse(
                evaluation_id=evaluation.id,
                definition_id=definition.id,
                work_item_id=evaluation.work_item_id,
                verification_id=evaluation.verification_id,
                sentence=definition.sentence,
                understood_as=plan.describe(),
                family=definition.family,
                verdict=evaluation.verdict,
                extracted_value=evaluation.extracted_value,
                calibrated_probability=evaluation.calibrated_probability,
                raw_score=evaluation.raw_score,
                evidence=list(evaluation.evidence or []),
                document_name=getattr(work_item, "original_filename", None),
                created_at=evaluation.created_at,
            )
        )
    return items


@router.post(
    "/workspaces/{workspace_id}/assertions/reviews/{evaluation_id}/resolve",
    response_model=AssertionReviewItemResponse,
)
def resolve_review(
    workspace_id: uuid.UUID,
    evaluation_id: uuid.UUID,
    payload: AssertionResolveRequest,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> AssertionReviewItemResponse:
    """"It passes", "It fails", or "Wrong paragraph"."""
    _assert_workspace(context, workspace_id)
    _gate(db, context, "assertion.review_resolve")

    evaluation = db.execute(
        select(AssertionEvaluation)
        .options(selectinload(AssertionEvaluation.definition))
        .where(
            AssertionEvaluation.id == evaluation_id,
            AssertionEvaluation.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if evaluation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found."
        )

    # ARCH40-S1:assertion-resolve-delegates. Same reasoning as
    # app/api/v1/verifications.py: the transition, the REVIEW_ITEM audit row
    # and `trigger.review.cleared` come from the one service the review hub
    # also calls, so a clause resolved here and a clause resolved in the hub
    # leave identical histories.
    #
    # `_resume_execution` stays and is still called. It is what
    # verify_arch33's "resolving a review re-enqueues automation.execute" gate
    # reads, and the shared service's requeue uses the same idempotency key,
    # so the second enqueue returns the first job rather than adding one.
    from app.services.review import resolution as review_resolution

    try:
        review_resolution.resolve_scoped(
            db,
            workspace_id=workspace_id,
            kind="ASSERTION",
            item_id=evaluation.id,
            actor_user_id=context.user_id,
            payload=review_resolution.ResolvePayload(
                reviewer_verdict=payload.reviewer_verdict,
                corrected_quote=payload.corrected_quote,
                corrected_value=payload.corrected_value,
            ),
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except review_resolution.ReviewResolutionError as exc:
        raise _bad_request(exc) from exc

    _resume_execution(db, evaluation=evaluation)
    db.commit()
    db.refresh(evaluation)

    definition = evaluation.definition
    plan = definition_service.plan_from_row(definition)
    return AssertionReviewItemResponse(
        evaluation_id=evaluation.id,
        definition_id=definition.id,
        work_item_id=evaluation.work_item_id,
        verification_id=evaluation.verification_id,
        sentence=definition.sentence,
        understood_as=plan.describe(),
        family=definition.family,
        verdict=evaluation.verdict,
        extracted_value=evaluation.extracted_value,
        calibrated_probability=evaluation.calibrated_probability,
        raw_score=evaluation.raw_score,
        evidence=list(evaluation.evidence or []),
        created_at=evaluation.created_at,
    )


def _resume_execution(db: Session, *, evaluation: AssertionEvaluation) -> None:
    """Re-enqueue the automation so it resumes on the reviewer's edge.

    The same shape `app/api/v1/verifications.py` uses, and the same reasoning:
    `automation.execute` already knows how to walk a graph and is idempotent
    on the outbox event id, so resolving a review enqueues rather than
    re-implementing the walk.

    The node executor reads the reviewer's verdict back out of
    `assertion_evaluations` on the re-run, so the second walk takes the edge
    the human chose rather than re-evaluating the document.
    """
    from app.models.outbox_event import OutboxEvent
    from app.services import job_service

    event = db.execute(
        select(OutboxEvent)
        .where(OutboxEvent.resource_id == evaluation.work_item_id)
        .order_by(OutboxEvent.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if event is None:
        logger.info(
            "assertion.no_event_to_resume",
            extra={"evaluation_id": str(evaluation.id)},
        )
        return

    job_service.enqueue(
        db,
        job_type="automation.execute",
        organization_id=evaluation.organization_id,
        payload={"outbox_event_id": str(event.id)},
        idempotency_key=f"automation:execute:assertion:{evaluation.id}",
    )


# ---------------------------------------------------------------------------
# Learned retrieval phrases
# ---------------------------------------------------------------------------


@router.get(
    "/workspaces/{workspace_id}/assertions/phrases",
    response_model=list[RetrievalPhraseResponse],
)
def list_learned_phrases(
    workspace_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireAdmin),
    family: Optional[str] = Query(default=None),
) -> list[RetrievalPhraseResponse]:
    """What this tenant's reviewers have taught retrieval.

    ADMIN, and organization-scoped rather than workspace-scoped, because the
    table is: §4.3 scopes the learned synonym table to the tenant, so a
    workspace-scoped read would show phrases learned elsewhere and imply they
    could be edited here.
    """
    _assert_workspace(context, workspace_id)
    _gate(db, context, "assertion.phrases")

    statement = (
        select(AssertionRetrievalPhrase)
        .where(AssertionRetrievalPhrase.organization_id == context.organization_id)
        .order_by(
            AssertionRetrievalPhrase.family.asc(),
            AssertionRetrievalPhrase.hits.desc(),
        )
        .limit(500)
    )
    if family:
        if family not in vocab.FAMILIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown assertion family {family!r}.",
            )
        statement = statement.where(AssertionRetrievalPhrase.family == family)

    return [
        RetrievalPhraseResponse.model_validate(row)
        for row in db.execute(statement).scalars().all()
    ]


__all__ = ["router"]


# ===========================================================================
# HARDENING-T3:D21 — clause checks authored from the console
# ===========================================================================

from pydantic import BaseModel as _BaseModel, Field as _Field  # noqa: E402


class ClauseCheckCreate(_BaseModel):
    name: str = _Field(min_length=1, max_length=120)


class ClauseCheckUpdate(_BaseModel):
    is_active: bool


class ClauseCheckResponse(_BaseModel):
    rule_id: uuid.UUID
    name: str
    is_active: bool
    node_key: str
    definition: Optional[AssertionDefinitionResponse] = None


def _clause_payload(check) -> ClauseCheckResponse:  # type: ignore[no-untyped-def]
    return ClauseCheckResponse(
        rule_id=check.rule.id,
        name=check.rule.name,
        is_active=bool(check.rule.is_active),
        node_key=check.node.node_key,
        definition=_definition_payload(check.definition) if check.definition is not None else None,
    )


@router.get(
    "/workspaces/{workspace_id}/assertions/clause-checks",
    response_model=list[ClauseCheckResponse],
    summary="List clause checks",
)
def list_clause_checks(
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> Any:
    from app.services.assertions import clause_check_service as svc

    _gate(db, context, "assertion.clause_check.list")
    return [_clause_payload(c) for c in svc.list_for_workspace(db, workspace_id=context.workspace_id)]


@router.post(
    "/workspaces/{workspace_id}/assertions/clause-checks",
    response_model=ClauseCheckResponse,
    status_code=201,
    summary="Create a clause check",
)
def create_clause_check(
    payload: ClauseCheckCreate,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireAdmin),
) -> Any:
    from app.services.assertions import clause_check_service as svc

    _gate(db, context, "assertion.clause_check.create")
    try:
        check = svc.create(db, workspace_id=context.workspace_id, created_by_user_id=context.user_id, name=payload.name)
    except svc.ClauseCheckError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return _clause_payload(check)


@router.patch(
    "/workspaces/{workspace_id}/assertions/clause-checks/{rule_id}",
    response_model=ClauseCheckResponse,
    summary="Turn a clause check on or off",
)
def update_clause_check(
    rule_id: uuid.UUID,
    payload: ClauseCheckUpdate,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireAdmin),
) -> Any:
    from app.services.assertions import clause_check_service as svc

    _gate(db, context, "assertion.clause_check.update")
    try:
        check = svc.set_active(db, workspace_id=context.workspace_id, rule_id=rule_id, is_active=payload.is_active)
    except svc.ClauseCheckError as exc:
        status_code = 404 if "does not exist" in str(exc) else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    db.commit()
    return _clause_payload(check)


@router.delete(
    "/workspaces/{workspace_id}/assertions/clause-checks/{rule_id}",
    status_code=204,
    response_model=None,
    response_class=Response,
    summary="Delete a clause check",
)
def delete_clause_check(
    rule_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireAdmin),
) -> Response:
    from app.services.assertions import clause_check_service as svc

    _gate(db, context, "assertion.clause_check.delete")
    try:
        svc.delete(db, workspace_id=context.workspace_id, rule_id=rule_id)
    except svc.ClauseCheckError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return Response(status_code=204)

