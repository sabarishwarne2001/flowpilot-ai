"""ARCH-40 — one resolution path for all three review sources.

THE HUB ADDS NO SEMANTICS
=========================

Each source keeps its own meaning of "resolved":

  EXTRACTION  `document_verification_service.resolve` writes the chosen field
              values, flips the verification to REVIEWED, and
              `emit_outcome` + a re-enqueued `automation.execute` release the
              blocked flow.
  ASSERTION   `assertions.triage.resolve_assertion` records the reviewer's
              verdict, teaches retrieval from a corrected quote, and the
              re-enqueued walk takes the edge the human chose.
  ANOMALY     status becomes CONFIRMED or DISMISSED, and a dismissal writes
              the layer-scoped suppression so a byte-identical re-upload does
              not re-raise it.

This module calls those three and adds nothing to them. What it does add is
the one thing none of them had: a consistent audit row.

WHY THE SOURCE ENDPOINTS CALL THIS TOO
======================================

Before ARCH-40, `POST .../verifications/{id}/resolve` and
`POST .../assertions/reviews/{id}/resolve` wrote no `audit_logs` row at all.
Only the anomaly endpoints did. A hub that wrote its own row would therefore
produce a *different* audit trail from the source endpoint for the same
decision — two ways to resolve one item, two different histories, and no way
to answer "who cleared this" without knowing which screen they used.

So `api/v1/verifications.py`, `api/v1/assertions.py` and `api/v1/anomalies.py`
all delegate here. `resolve_item` is the only writer of the REVIEW_ITEM audit
row, which makes gate B5 ("the same audit row either way") true by
construction rather than by two implementations happening to agree.

The anomaly endpoints' pre-existing WORK_ITEM audit row is deliberately left
in place and untouched: `verify_arch34.py` asserts it, and removing a passing
gate's subject to tidy up is how a regression gets shipped.

THE TRIGGER
===========

`trigger.review.cleared` is emitted through `outbox_service.emit_trigger`,
which writes the INTERNAL event and its `automation.execute` job in the
caller's transaction. Nothing relays INTERNAL events, so an event written
without its job is an event no rule ever sees — and committing inside
`emit_trigger` would break the atomicity the whole outbox depends on. Neither
happens here: no commit, and the emit is the last thing before returning.

ASSIGNMENT
==========

`assign` and `unassign` live here rather than in their own module because an
assignment is only meaningful against an item the caller can see, and
`projection.load_item` is the only workspace-scoped way to establish that.
The database enforces the rest: the composite foreign key onto
`workspace_members (user_id, workspace_id)` refuses an assignee who is not a
member, and cascades the assignment away when their membership ends.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.services.review import vocabulary as vocab
from app.services.review.projection import ReviewItem, load_item

logger = logging.getLogger("app.services.review.resolution")

#: ARCH40-S1:review-cleared-event. Reserved by
#: arch40_step0_review_vocabulary and listed in
#: app/core/automation_events.TRIGGER_NATIVE_EVENT_TYPES.
REVIEW_CLEARED_EVENT: str = "trigger.review.cleared"

ANOMALY_VERDICT_CONFIRM: str = "CONFIRM"
ANOMALY_VERDICT_DISMISS: str = "DISMISS"
ANOMALY_VERDICTS: tuple[str, ...] = (ANOMALY_VERDICT_CONFIRM, ANOMALY_VERDICT_DISMISS)


class ReviewResolutionError(ValueError):
    """The payload does not fit the kind, or the item cannot be resolved."""


@dataclass
class ResolvePayload:
    """Everything the three sources between them need, validated per kind."""

    #: EXTRACTION — the chosen value per field path.
    values: Optional[dict[str, Any]] = None
    #: ASSERTION — PASS, FAIL or UNDETERMINED, plus optional corrections.
    reviewer_verdict: Optional[str] = None
    corrected_quote: Optional[str] = None
    corrected_value: Optional[dict[str, Any]] = None
    #: ANOMALY — CONFIRM or DISMISS, with a reason DISMISS requires.
    anomaly_verdict: Optional[str] = None
    note: Optional[str] = None
    ttl_days: Optional[int] = None
    #: ARCH42-S1:merge-verdict. MERGE -- MERGE or SEPARATE.
    merge_verdict: Optional[str] = None
    #: ARCH43-S1:split-verdict. SPLIT -- APPROVE (optionally with corrected
    #: boundaries: the first page of every document after the first) or REJECT.
    split_verdict: Optional[str] = None
    split_boundaries: Optional[list[int]] = None
    #: ARCH44-S1:table-verdict. TABLE -- ACCEPT (the figures are right as they
    #: stand, after any corrections) or REJECT (the table is unusable).
    table_verdict: Optional[str] = None
    #: ARCH45-S1:corroboration-verdict. CORROBORATION -- CONFIRM (the material
    #: differences are real) or DISMISS (they are not), for every open one.
    corroboration_verdict: Optional[str] = None


@dataclass
class ResolutionOutcome:
    kind: str
    item_id: uuid.UUID
    work_item_id: Optional[uuid.UUID]
    detail: str
    audit_log_id: Optional[uuid.UUID] = None


def _audit(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    item: ReviewItem,
    detail: str,
) -> Optional[uuid.UUID]:
    """The canonical review audit row. Written on every resolution path.

    `details` deliberately carries no marker of which endpoint was used. Gate
    B5 compares the row produced through the hub with the row produced through
    the source's own endpoint and requires them to be equal; a `via` field
    would make that comparison pass only by being excluded from it.
    """
    from app.services import audit_service

    entry = audit_service.record(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_id=actor_user_id,
        resource_type=AuditResourceType.REVIEW_ITEM,
        resource_id=item.item_id,
        action=AuditAction.UPDATED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "review_kind": item.kind,
            "review_item_id": str(item.item_id),
            "work_item_id": str(item.work_item_id) if item.work_item_id else None,
            "severity": item.severity,
            "resolution": detail,
        },
    )
    return getattr(entry, "id", None)


def _emit_cleared(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    item: ReviewItem,
    detail: str,
) -> None:
    from app.services import outbox_service

    outbox_service.emit_trigger(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        event_type=REVIEW_CLEARED_EVENT,
        resource_id=item.work_item_id or item.item_id,
        payload={
            "review_kind": item.kind,
            "review_item_id": str(item.item_id),
            "work_item_id": str(item.work_item_id) if item.work_item_id else None,
            "severity": item.severity,
            "resolution": detail,
        },
        idempotency_key=f"review:cleared:{item.kind}:{item.item_id}",
    )


# ---------------------------------------------------------------------------
# Per-kind transitions
# ---------------------------------------------------------------------------


def _resolve_extraction(
    db: Session, *, item: ReviewItem, actor_user_id: uuid.UUID, payload: ResolvePayload
) -> str:
    from app.models.verification import DocumentVerification
    from app.services import document_verification_service as dv

    if payload.values is None:
        raise ReviewResolutionError(
            "An extraction review needs the chosen value for each disagreeing "
            "field."
        )

    verification = db.execute(
        select(DocumentVerification).where(
            DocumentVerification.id == item.item_id,
            DocumentVerification.workspace_id == item.workspace_id,
        )
    ).scalar_one_or_none()
    if verification is None:
        raise ReviewResolutionError("This extraction review no longer exists.")

    try:
        dv.resolve(
            db,
            verification=verification,
            chosen=dict(payload.values),
            reviewer_user_id=actor_user_id,
        )
    except dv.VerificationError as exc:
        raise ReviewResolutionError(str(exc)) from exc

    dv.emit_outcome(db, verification=verification)
    _requeue_automation(
        db,
        organization_id=verification.organization_id,
        work_item_id=verification.work_item_id,
        event_type="work_item.verification_completed",
    )
    return "REVIEWED"


def _resolve_assertion(
    db: Session, *, item: ReviewItem, actor_user_id: uuid.UUID, payload: ResolvePayload
) -> str:
    from sqlalchemy.orm import selectinload

    from app.models.assertion import AssertionEvaluation
    from app.services.assertions import triage as triage_service
    from app.services.assertions import vocabulary as assertion_vocab

    verdict = (payload.reviewer_verdict or "").strip().upper()
    if verdict not in assertion_vocab.VERDICTS:
        raise ReviewResolutionError(
            f"'{payload.reviewer_verdict}' is not a verdict. Expected one of: "
            f"{', '.join(assertion_vocab.VERDICTS)}."
        )

    evaluation = db.execute(
        select(AssertionEvaluation)
        .options(selectinload(AssertionEvaluation.definition))
        .where(
            AssertionEvaluation.id == item.item_id,
            AssertionEvaluation.workspace_id == item.workspace_id,
        )
    ).scalar_one_or_none()
    if evaluation is None:
        raise ReviewResolutionError("This clause review no longer exists.")

    try:
        triage_service.resolve_assertion(
            db,
            evaluation=evaluation,
            reviewer_verdict=verdict,
            reviewer_user_id=actor_user_id,
            corrected_quote=payload.corrected_quote,
            corrected_value=payload.corrected_value,
        )
    except ValueError as exc:
        raise ReviewResolutionError(str(exc)) from exc

    _requeue_automation(
        db,
        organization_id=evaluation.organization_id,
        work_item_id=evaluation.work_item_id,
        event_type="trigger.assertion.held",
    )
    return verdict


def resolve_anomaly_transition(
    db: Session,
    *,
    finding: Any,
    actor_user_id: uuid.UUID,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    payload: ResolvePayload,
) -> str:
    """The anomaly state change, shared by the hub and `api/v1/anomalies.py`.

    Exported rather than private because the anomaly endpoints call it
    directly: they own the 409 on an already-closed finding and their own
    response shape, and this is the part that must not be written twice.

    The pre-existing WORK_ITEM audit row is written by the caller, unchanged,
    because `verify_arch34.py` asserts it.
    """
    from datetime import datetime, timezone

    from app.services.radar import suppressions as suppressions_module
    from app.services.radar import vocabulary as radar_vocab

    verdict = (payload.anomaly_verdict or "").strip().upper()
    if verdict not in ANOMALY_VERDICTS:
        raise ReviewResolutionError(
            f"'{payload.anomaly_verdict}' is not an anomaly verdict. Expected "
            f"one of: {', '.join(ANOMALY_VERDICTS)}."
        )
    if finding.status != radar_vocab.STATUS_OPEN:
        raise ReviewResolutionError(
            f"This finding was already {finding.status.lower()}. Reopening a "
            "closed finding would discard the decision and the person who "
            "made it."
        )

    # HARDENING-T1:D33. The actor and time are stamped BEFORE any status
    # change. They were set at the end, after `suppress_pair` /
    # `suppress_series`, whose db.flush() wrote the DISMISSED status with no
    # resolver — which `ck_af_resolution_has_actor` refuses. Every dismissal
    # of a finding with a counterpart (every duplicate) failed with 500, on
    # the anomaly page and in the Review Hub alike.
    finding.resolved_by_user_id = actor_user_id
    finding.resolved_at = datetime.now(timezone.utc)
    if verdict == ANOMALY_VERDICT_CONFIRM:
        finding.status = radar_vocab.STATUS_CONFIRMED
        finding.resolution_note = (payload.note or "").strip() or None
    else:
        reason = (payload.note or "").strip()
        if len(reason) < 10:
            raise ReviewResolutionError(
                "Dismissing a finding needs a reason of at least 10 characters. "
                "The reason is what the next reviewer reads instead of "
                "re-deciding."
            )
        finding.status = radar_vocab.STATUS_DISMISSED
        finding.resolution_note = reason
        if finding.counterpart_work_item_id is not None:
            suppressions_module.suppress_pair(
                db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                layer=finding.layer,
                item_a_id=finding.subject_work_item_id,
                item_b_id=finding.counterpart_work_item_id,
                reason=reason,
                created_by_user_id=actor_user_id,
                ttl_days=payload.ttl_days,
            )
        else:
            metrics = finding.metrics or {}
            suppressions_module.suppress_series(
                db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                item_a_id=finding.subject_work_item_id,
                vendor_key=str(metrics.get("vendor_key") or ""),
                sku=metrics.get("sku"),
                reason=reason,
                created_by_user_id=actor_user_id,
                ttl_days=payload.ttl_days,
            )

    return str(finding.status)


def _resolve_anomaly(
    db: Session, *, item: ReviewItem, actor_user_id: uuid.UUID, payload: ResolvePayload
) -> str:
    from app.models.radar import AnomalyFinding

    finding = db.execute(
        select(AnomalyFinding).where(
            AnomalyFinding.id == item.item_id,
            AnomalyFinding.workspace_id == item.workspace_id,
        )
    ).scalar_one_or_none()
    if finding is None:
        raise ReviewResolutionError("This finding no longer exists.")

    return resolve_anomaly_transition(
        db,
        finding=finding,
        actor_user_id=actor_user_id,
        organization_id=item.organization_id,
        workspace_id=item.workspace_id,
        payload=payload,
    )


def _requeue_automation(
    db: Session,
    *,
    organization_id: uuid.UUID,
    work_item_id: Optional[uuid.UUID],
    event_type: str,
) -> None:
    """Re-enqueue the walk that the held review paused.

    The same shape `api/v1/verifications.py` and `api/v1/assertions.py` both
    used before ARCH-40, and for the same reason: `automation.execute` already
    knows how to walk a graph and is idempotent on the outbox event id, so
    resolving enqueues rather than re-implementing the walk.
    """
    if work_item_id is None:
        return

    from app.models.outbox_event import OutboxEvent
    from app.services import job_service

    event = db.execute(
        select(OutboxEvent)
        .where(
            OutboxEvent.resource_id == work_item_id,
            OutboxEvent.event_type == event_type,
        )
        .order_by(OutboxEvent.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if event is None:
        return

    job_service.enqueue(
        db,
        job_type="automation.execute",
        organization_id=organization_id,
        payload={"outbox_event_id": str(event.id)},
        idempotency_key=f"automation:execute:{event.id}",
    )


def _resolve_merge(
    db: Session, *, item: ReviewItem, actor_user_id: uuid.UUID, payload: ResolvePayload
) -> str:
    """ARCH42-S1:resolve-merge. A person decides whether two records are one.

    MERGE points the newer record at the older (reversible from Entity 360),
    overriding the conflict guard: the guard exists to ask exactly this
    person. SEPARATE is remembered, so no nightly sweep proposes the pair
    again. Either way the mention that raised the question is CONFIRMED.
    """
    from app.models.entity_graph import EntityMention, EntityMergeCandidate
    from app.services.entities import graph
    from app.services.entities import vocabulary as ev

    verdict = (payload.merge_verdict or "").strip().upper()
    if verdict not in ev.MERGE_VERDICTS:
        raise ReviewResolutionError("A merge review needs merge_verdict: MERGE or SEPARATE.")
    candidate = db.execute(
        select(EntityMergeCandidate).where(
            EntityMergeCandidate.id == item.item_id,
            EntityMergeCandidate.workspace_id == item.workspace_id,
        )
    ).scalar_one_or_none()
    if candidate is None:
        raise ReviewResolutionError("This merge proposal no longer exists.")
    if candidate.status != ev.CANDIDATE_OPEN:
        raise ReviewResolutionError("This merge proposal has already been decided.")
    if verdict == ev.VERDICT_MERGE:
        left = graph.root_of(db, candidate.left_entity_id)
        right = graph.root_of(db, candidate.right_entity_id)
        loser, winner = (left, right) if left.created_at >= right.created_at else (right, left)
        graph.merge(db, loser_id=loser.id, winner_id=winner.id, actor_user_id=actor_user_id,
                    reason=ev.MERGE_REASON_REVIEW, allow_conflict=True)
        candidate.status = ev.CANDIDATE_MERGED
    else:
        candidate.status = ev.CANDIDATE_SEPARATE
    candidate.resolved_at = graph.now()
    candidate.resolved_by_user_id = actor_user_id
    if candidate.mention_id is not None:
        mention = db.get(EntityMention, candidate.mention_id)
        if mention is not None and mention.decision == ev.DECISION_REVIEW:
            mention.decision, mention.decided_by_user_id = ev.DECISION_CONFIRMED, actor_user_id
    db.flush()
    return verdict


def _resolve_split(
    db: Session, *, item: ReviewItem, actor_user_id: uuid.UUID, payload: ResolvePayload
) -> str:
    """ARCH43-S1:resolve-split. A person decides how a scanned packet divides.

    APPROVE (with the reviewer's corrected boundaries, if given) enqueues the
    split; REJECT keeps the packet as one document. Nothing is cut until a
    person has approved the plan.
    """
    from app.models.packets import PacketSplit
    from app.services.packets import service as packet_service
    from app.services.packets import vocabulary as pv

    verdict = (payload.split_verdict or "").strip().upper()
    if verdict not in pv.SPLIT_VERDICTS:
        raise ReviewResolutionError("A split review needs split_verdict: APPROVE or REJECT.")
    split = db.execute(
        select(PacketSplit).where(PacketSplit.id == item.item_id, PacketSplit.workspace_id == item.workspace_id)
    ).scalar_one_or_none()
    if split is None:
        raise ReviewResolutionError("This split plan no longer exists.")
    if split.status != pv.STATUS_PROPOSED:
        raise ReviewResolutionError("This split plan has already been decided.")
    try:
        if verdict == pv.VERDICT_APPROVE:
            packet_service.approve(db, split=split, actor_user_id=actor_user_id, boundaries=payload.split_boundaries)
        else:
            packet_service.reject(db, split=split, actor_user_id=actor_user_id)
    except packet_service.PacketError as exc:
        raise ReviewResolutionError(str(exc)) from exc
    return f"{verdict} (corrected)" if payload.split_boundaries is not None and verdict == pv.VERDICT_APPROVE else verdict


def _resolve_table(
    db: Session, *, item: ReviewItem, actor_user_id: uuid.UUID, payload: ResolvePayload
) -> str:
    """ARCH44-S1:resolve-table. A person decides a table whose figures do not
    reconcile. Corrections happen cell by cell in the table viewer (a table
    whose figures then reconcile leaves the hub by itself); here the reviewer
    accepts the figures as they stand or rejects the table."""
    from app.models.tables import ExtractedTable
    from app.services.tables import service as table_service
    from app.services.tables import vocabulary as tv

    verdict = (payload.table_verdict or "").strip().upper()
    if verdict not in tv.TABLE_VERDICTS:
        raise ReviewResolutionError("A table review needs table_verdict: ACCEPT or REJECT.")
    table = db.execute(
        select(ExtractedTable).where(ExtractedTable.id == item.item_id, ExtractedTable.workspace_id == item.workspace_id)
    ).scalar_one_or_none()
    if table is None:
        raise ReviewResolutionError("This table no longer exists.")
    if table.status != tv.STATUS_FLAGGED:
        raise ReviewResolutionError("This table has already been decided or no longer needs review.")
    try:
        table_service.review(db, table=table, verdict=verdict, actor_user_id=actor_user_id)
    except table_service.TableError as exc:
        raise ReviewResolutionError(str(exc)) from exc
    return verdict


def _resolve_corroboration(
    db: Session, *, item: ReviewItem, actor_user_id: uuid.UUID, payload: ResolvePayload
) -> str:
    """ARCH45-S1:resolve-corroboration. A person decides a comparison whose
    documents disagree. Individual differences are decided on the comparison's
    page (a run whose every material difference is decided leaves the hub by
    itself); here the reviewer confirms or dismisses all that remain open."""
    from app.models.corroboration import CorroborationRun
    from app.services.corroboration import service as corroboration_service
    from app.services.corroboration import vocabulary as cv

    verdict = (payload.corroboration_verdict or "").strip().upper()
    if verdict not in cv.RUN_VERDICTS:
        raise ReviewResolutionError("A comparison review needs corroboration_verdict: CONFIRM or DISMISS.")
    run = db.execute(
        select(CorroborationRun).where(CorroborationRun.id == item.item_id,
                                       CorroborationRun.workspace_id == item.workspace_id)
    ).scalar_one_or_none()
    if run is None:
        raise ReviewResolutionError("This comparison no longer exists.")
    try:
        corroboration_service.review_run(db, run=run, verdict=verdict, actor_user_id=actor_user_id)
    except corroboration_service.CorroborationError as exc:
        raise ReviewResolutionError(str(exc)) from exc
    return verdict


_DISPATCH = {
    vocab.KIND_EXTRACTION: _resolve_extraction,
    vocab.KIND_ASSERTION: _resolve_assertion,
    vocab.KIND_ANOMALY: _resolve_anomaly,
    vocab.KIND_MERGE: _resolve_merge,  # ARCH42-S1:resolve-merge
    vocab.KIND_SPLIT: _resolve_split,  # ARCH43-S1:resolve-split
    vocab.KIND_TABLE: _resolve_table,  # ARCH44-S1:resolve-table
    vocab.KIND_CORROBORATION: _resolve_corroboration,  # ARCH45-S1:resolve-corroboration
}


# ---------------------------------------------------------------------------
# The single entry point
# ---------------------------------------------------------------------------


def resolve_item(
    db: Session,
    *,
    item: ReviewItem,
    actor_user_id: uuid.UUID,
    payload: ResolvePayload,
) -> ResolutionOutcome:
    """Dispatch to the owning service, audit it, announce it.

    Does not commit. The caller owns the transaction, which is what lets the
    outbox event, its job and the audit row land atomically with the state
    change.
    """
    if item.status == vocab.STATUS_RESOLVED:
        raise ReviewResolutionError(
            "This item has already been resolved. Reopening it would discard "
            "the decision and the person who made it."
        )

    handler = _DISPATCH.get(item.kind)
    if handler is None:  # pragma: no cover - the CHECK and the view agree
        raise ReviewResolutionError(f"'{item.kind}' is not a review kind.")

    detail = handler(db, item=item, actor_user_id=actor_user_id, payload=payload)

    audit_log_id = _audit(
        db,
        organization_id=item.organization_id,
        workspace_id=item.workspace_id,
        actor_user_id=actor_user_id,
        item=item,
        detail=detail,
    )

    # An assignment outlives nothing. Clearing it here keeps "assigned to me"
    # meaning "waiting on me" rather than "was once mine".
    clear_assignment(db, workspace_id=item.workspace_id, kind=item.kind, item_id=item.item_id)

    _emit_cleared(
        db,
        organization_id=item.organization_id,
        workspace_id=item.workspace_id,
        item=item,
        detail=detail,
    )

    logger.info(
        "review.resolved",
        extra={
            "review_kind": item.kind,
            "review_item_id": str(item.item_id),
            "workspace_id": str(item.workspace_id),
            "actor_id": str(actor_user_id),
        },
    )
    return ResolutionOutcome(
        kind=item.kind,
        item_id=item.item_id,
        work_item_id=item.work_item_id,
        detail=detail,
        audit_log_id=audit_log_id,
    )


def resolve_scoped(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    kind: str,
    item_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    payload: ResolvePayload,
) -> ResolutionOutcome:
    """Load the item inside the workspace, then resolve it.

    The load is the isolation boundary: `projection.load_item` reads the view
    through `_queue_cte`, which carries the workspace predicate. An item from
    another workspace is not found, so resolving it is a 404 rather than a
    cross-tenant write. Gate B3 and mutant M3 both target this.
    """
    item = load_item(db, workspace_id=workspace_id, kind=kind, item_id=item_id)
    if item is None:
        raise LookupError("Review item not found in this workspace.")
    return resolve_item(db, item=item, actor_user_id=actor_user_id, payload=payload)


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


def assign(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    kind: str,
    item_id: uuid.UUID,
    assignee_user_id: uuid.UUID,
    assigned_by_user_id: uuid.UUID,
) -> None:
    """Give an item to a workspace member, replacing any earlier owner.

    Membership is not re-checked in Python. The composite foreign key onto
    `workspace_members (user_id, workspace_id)` refuses a non-member, so a
    Python check would be a second, weaker copy of a rule the database already
    holds — and the kind that drifts.
    """
    from sqlalchemy.exc import IntegrityError

    from app.models.review import ReviewAssignment

    item = load_item(db, workspace_id=workspace_id, kind=kind, item_id=item_id)
    if item is None:
        raise LookupError("Review item not found in this workspace.")

    existing = db.execute(
        select(ReviewAssignment).where(
            ReviewAssignment.kind == kind, ReviewAssignment.item_id == item_id
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.assignee_user_id = assignee_user_id
        existing.assigned_by_user_id = assigned_by_user_id
    else:
        db.add(
            ReviewAssignment(
                kind=kind,
                item_id=item_id,
                workspace_id=workspace_id,
                assignee_user_id=assignee_user_id,
                assigned_by_user_id=assigned_by_user_id,
            )
        )

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ReviewResolutionError(
            "That person is not a member of this workspace, so they cannot be "
            "assigned an item in it."
        ) from exc


def clear_assignment(
    db: Session, *, workspace_id: uuid.UUID, kind: str, item_id: uuid.UUID
) -> bool:
    from app.models.review import ReviewAssignment

    row = db.execute(
        select(ReviewAssignment).where(
            ReviewAssignment.kind == kind,
            ReviewAssignment.item_id == item_id,
            ReviewAssignment.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


__all__ = [
    "ANOMALY_VERDICTS",
    "ANOMALY_VERDICT_CONFIRM",
    "ANOMALY_VERDICT_DISMISS",
    "REVIEW_CLEARED_EVENT",
    "ResolutionOutcome",
    "ResolvePayload",
    "ReviewResolutionError",
    "assign",
    "clear_assignment",
    "resolve_anomaly_transition",
    "resolve_item",
    "resolve_scoped",
]
