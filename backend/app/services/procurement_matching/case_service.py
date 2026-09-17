"""ARCH-31 Step 3 — case persistence, lifecycle, events and metering.

WHAT THIS MODULE OWNS
=====================

`matcher.match()` is pure and knows nothing about rows. This module is the
only place that turns a `MatchResult` into database state, and the only place
that decides what a case's status becomes. Splitting it that way is what lets
the matcher be re-run 25 times in a gate without writing anything.

THE RE-SCORE CONTRACT, WHICH IS THE WHOLE DESIGN
================================================

    same digest        -> nothing happens. Not an update, not a touch. The
                          sweep runs every few minutes and most invocations
                          must be free.

    different digest    -> the live case is SUPERSEDED and a new one opens.
       + live case was      Never edited in place: `procurement_cases` rows are
         unresolved         what a reviewer looked at, and re-writing the
                            numbers under a case somebody is mid-way through
                            reading is how a reviewer approves a line they
                            never saw.

    different digest    -> the resolved case is SUPERSEDED and a new one opens
       + live case was      too, with MATCH_RESCORED audited. An APPROVED case
         APPROVED or        is a record that a person authorised THOSE numbers.
         DISPUTED           Mutating it would retro-fit their signature onto
                            figures they never approved, which is the one
                            thing an audit trail exists to prevent.

`uq_procurement_cases_live_invoice` enforces the "one live case" half at the
database, so a concurrent scorer racing this logic loses on an IntegrityError
rather than producing two live cases.

STATUS ON OPEN
==============

    MATCHED       no red lines and no self-inconsistency finding.
    NEEDS_REVIEW  anything else.

There is no OPEN-then-transition dance. A case is scored the moment it is
created, so a status of OPEN would describe a state that exists for
microseconds and would show in the queue as a third thing a reviewer has to
learn the meaning of. OPEN remains in the vocabulary because a case created by
`rematch` before its score lands is legitimately open.

METERING, EXACTLY ONCE PER DIGEST
=================================

`usage_service.record_usage(idempotency_key=f"procurement.case:{digest}")`.
The digest, not the case id: a case that is superseded and re-scored under the
SAME inputs (which cannot happen, the digest would match) must not bill twice,
and more importantly a case re-scored under a NEW policy is genuinely new work
and SHOULD bill. Keying on the case id would bill once per row and therefore
once per policy edit, which turns "tighten your tolerance" into a charge.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.procurement import (
    ProcurementCase,
    ProcurementCaseLine,
    ProcurementTolerancePolicy,
)
from app.services import audit_service, outbox_service, usage_service
from app.services.procurement_matching import digest as digest_module
from app.services.procurement_matching import line_extraction, matcher as matcher_module
from app.services.procurement_matching import policy as policy_module
from app.services.procurement_matching import similarity as similarity_module
from app.services.procurement_matching.vocabulary import (
    CASE_STATUS_APPROVED,
    CASE_STATUS_DISPUTED,
    CASE_STATUS_MATCHED,
    CASE_STATUS_NEEDS_REVIEW,
    CASE_STATUS_SUPERSEDED,
    RED_OUTCOMES,
    RESOLVED_CASE_STATUSES,
)

logger = logging.getLogger("app.services.procurement_matching.case_service")

__all__ = [
    "CaseServiceError",
    "OverrideReasonRequired",
    "CaseNotLive",
    "ScoreOutcome",
    "score_case",
    "approve_case",
    "dispute_case",
    "get_case",
    "list_cases",
    "publish_policy",
    "impact_preview",
    "USAGE_EVENT_PROCUREMENT_CASE",
    "MINIMUM_DISPUTE_REASON",
]

USAGE_EVENT_PROCUREMENT_CASE = "procurement.case"
MINIMUM_DISPUTE_REASON = 10

EVENT_COMPLETED = "procurement.completed"
EVENT_APPROVED = "procurement.approved"
EVENT_DISPUTED = "procurement.disputed"


class CaseServiceError(Exception):
    """Base for refusals this module raises."""


class OverrideReasonRequired(CaseServiceError):
    """Approving a case with red lines needs a written reason."""


class CaseNotLive(CaseServiceError):
    """The case has been superseded or already resolved."""


class ScoreOutcome:
    """What a scoring pass did, so the caller can report it without re-reading."""

    __slots__ = ("case", "created", "superseded_case_id", "digest", "unchanged")

    def __init__(
        self,
        *,
        case: Optional[ProcurementCase],
        created: bool,
        superseded_case_id: Optional[uuid.UUID],
        digest: str,
        unchanged: bool,
    ) -> None:
        self.case = case
        self.created = created
        self.superseded_case_id = superseded_case_id
        self.digest = digest
        self.unchanged = unchanged

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": str(self.case.id) if self.case else None,
            "created": self.created,
            "unchanged": self.unchanged,
            "superseded_case_id": (
                str(self.superseded_case_id) if self.superseded_case_id else None
            ),
            "input_digest": self.digest,
        }


# ---------------------------------------------------------------------------
# Reading documents
# ---------------------------------------------------------------------------


def _workspace_settings(db: Session, workspace_id: uuid.UUID) -> tuple[Optional[str], Optional[str]]:
    """(currency, date_format) — ARCH-30 A2's two settings.

    Read once per case and threaded into every document. Reading them per
    document would be identical today and is exactly the kind of thing that
    drifts into "the PO used the default and the invoice used the setting",
    which surfaces as a phantom variance nobody can reproduce.
    """
    from app.models.workspace import Workspace

    row = db.execute(
        select(Workspace.currency, Workspace.date_format).where(
            Workspace.id == workspace_id
        )
    ).one_or_none()
    if row is None:
        return None, None
    return row[0], row[1]


def _document_for(
    db: Session,
    work_item_id: Optional[uuid.UUID],
    *,
    workspace_id: uuid.UUID,
    currency: Optional[str],
    date_format: Optional[str],
) -> Optional[line_extraction.DocumentLines]:
    if work_item_id is None:
        return None

    from app.models.work_item import WorkItem

    work_item = db.execute(
        select(WorkItem).where(
            WorkItem.id == work_item_id, WorkItem.workspace_id == workspace_id
        )
    ).scalar_one_or_none()
    if work_item is None:
        # ARCH-02. A work item that is not in this workspace is not merely
        # absent — asking for it across the boundary is the bug, and
        # returning None rather than raising lets the case be built from
        # whatever is legitimately present.
        return None
    return line_extraction.extract_lines(
        work_item.extracted_entities,
        workspace_currency=currency,
        date_format=date_format,
    )


def _evidence_for(
    line: Optional[Any], *, work_item_id: Optional[uuid.UUID]
) -> Optional[dict[str, Any]]:
    """The Step 0 evidence-pointer shape, or None when there is nothing to point at."""
    if line is None or work_item_id is None:
        return None
    pointer: dict[str, Any] = {
        "work_item_id": str(work_item_id),
        "line_index": line.index,
    }
    evidence = getattr(line, "evidence", None) or {}
    for key in ("page", "char_start", "char_end", "bbox"):
        if key in evidence:
            pointer[key] = evidence[key]
    return pointer


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_case(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    invoice_work_item_id: Optional[uuid.UUID],
    po_work_item_id: Optional[uuid.UUID] = None,
    receipt_work_item_id: Optional[uuid.UUID] = None,
    vendor_key: Optional[str] = None,
    actor_id: Optional[uuid.UUID] = None,
    backend_id: Optional[str] = None,
) -> ScoreOutcome:
    """Score one document set, persisting only when the digest moved."""
    present = [
        item
        for item in (po_work_item_id, receipt_work_item_id, invoice_work_item_id)
        if item is not None
    ]
    if len(present) < 2:
        raise CaseServiceError(
            "a case needs at least two documents; one document is not a match, "
            "it is a document (ck_procurement_cases_two_sided)"
        )

    currency, date_format = _workspace_settings(db, workspace_id)
    documents = {
        "po": _document_for(db, po_work_item_id, workspace_id=workspace_id, currency=currency, date_format=date_format),
        "receipt": _document_for(db, receipt_work_item_id, workspace_id=workspace_id, currency=currency, date_format=date_format),
        "invoice": _document_for(db, invoice_work_item_id, workspace_id=workspace_id, currency=currency, date_format=date_format),
    }

    policy = policy_module.resolve_policy(db, workspace_id=workspace_id)
    backend = similarity_module.get_backend(backend_id)

    computed_digest = digest_module.input_digest(
        po=documents["po"],
        receipt=documents["receipt"],
        invoice=documents["invoice"],
        po_work_item_id=po_work_item_id,
        receipt_work_item_id=receipt_work_item_id,
        invoice_work_item_id=invoice_work_item_id,
        policy=policy,
        backend_id=backend.backend_id,
    )

    existing = _live_case_for(db, workspace_id=workspace_id, invoice_work_item_id=invoice_work_item_id)

    if existing is not None and existing.input_digest == computed_digest:
        # The common path on a sweep. Deliberately writes nothing at all —
        # not even updated_at — so that "when did this case last change?"
        # keeps meaning something.
        return ScoreOutcome(
            case=existing, created=False, superseded_case_id=None,
            digest=computed_digest, unchanged=True,
        )

    result = matcher_module.match(
        po=documents["po"],
        receipt=documents["receipt"],
        invoice=documents["invoice"],
        policy=policy,
        backend=backend,
    )

    superseded_id: Optional[uuid.UUID] = None
    if existing is not None:
        superseded_id = existing.id
        was_resolved = existing.status in RESOLVED_CASE_STATUSES
        existing.status = CASE_STATUS_SUPERSEDED
        db.flush([existing])
        audit_service.record(
            db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            resource_type=AuditResourceType.PROCUREMENT_CASE,
            resource_id=existing.id,
            action=AuditAction.MATCH_RESCORED,
            details={
                "previous_digest": existing.input_digest,
                "new_digest": computed_digest,
                "previous_status": CASE_STATUS_SUPERSEDED,
                "was_resolved": was_resolved,
                "policy_version": result.policy_version,
            },
        )

    case = _persist(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        po_work_item_id=po_work_item_id,
        receipt_work_item_id=receipt_work_item_id,
        invoice_work_item_id=invoice_work_item_id,
        vendor_key=vendor_key,
        digest=computed_digest,
        result=result,
    )

    _meter_once(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        case=case,
        digest=computed_digest,
    )

    # ARCH37-S1:procurement-twins. The twin names the invoice as its
    # resource: the automation handler loads resource_id as a work item.
    outbox_service.emit_public_with_twin(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        event_type=EVENT_COMPLETED,
        resource_id=case.id,
        twin_resource_id=invoice_work_item_id,
        idempotency_key=f"{EVENT_COMPLETED}:{computed_digest}",
        payload={
            "case_id": str(case.id),
            "status": case.status,
            "exception_count": case.exception_count,
            "variance_micros": case.variance_micros,
            "policy_version": case.policy_version,
            "line_count": case.line_count,
        },
    )

    return ScoreOutcome(
        case=case, created=True, superseded_case_id=superseded_id,
        digest=computed_digest, unchanged=False,
    )


def _live_case_for(
    db: Session, *, workspace_id: uuid.UUID, invoice_work_item_id: Optional[uuid.UUID]
) -> Optional[ProcurementCase]:
    if invoice_work_item_id is None:
        return None
    return db.execute(
        select(ProcurementCase).where(
            ProcurementCase.workspace_id == workspace_id,
            ProcurementCase.invoice_work_item_id == invoice_work_item_id,
            ProcurementCase.status != CASE_STATUS_SUPERSEDED,
        )
    ).scalar_one_or_none()


def _persist(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    po_work_item_id: Optional[uuid.UUID],
    receipt_work_item_id: Optional[uuid.UUID],
    invoice_work_item_id: Optional[uuid.UUID],
    vendor_key: Optional[str],
    digest: str,
    result: Any,
) -> ProcurementCase:
    exception_count = result.exception_count
    self_inconsistent = any(
        finding.get("code") == "SELF_INCONSISTENT_TOTAL"
        for finding in result.header_findings
    )
    status = (
        CASE_STATUS_MATCHED
        if exception_count == 0 and not self_inconsistent
        else CASE_STATUS_NEEDS_REVIEW
    )

    case = ProcurementCase(
        organization_id=organization_id,
        workspace_id=workspace_id,
        po_work_item_id=po_work_item_id,
        receipt_work_item_id=receipt_work_item_id,
        invoice_work_item_id=invoice_work_item_id,
        status=status,
        input_digest=digest,
        policy_version=result.policy_version,
        vendor_key=vendor_key,
        header_findings=list(result.header_findings),
        line_count=len(result.lines),
        exception_count=exception_count,
        variance_micros=result.variance_micros,
    )
    db.add(case)
    try:
        with db.begin_nested():
            db.flush([case])
    except IntegrityError:
        # uq_procurement_cases_live_invoice. A concurrent scorer won the
        # race; its case is as good as this one (same inputs would produce
        # the same digest) so yield rather than retry into a livelock.
        db.expunge(case)
        winner = _live_case_for(
            db, workspace_id=workspace_id, invoice_work_item_id=invoice_work_item_id
        )
        if winner is not None:
            logger.info(
                "procurement.case_race_lost",
                extra={"workspace_id": str(workspace_id), "winner": str(winner.id)},
            )
            return winner
        raise

    for line in result.lines:
        evidence: dict[str, Any] = {}
        for side, source, work_item_id in (
            ("po", line.po_line, po_work_item_id),
            ("receipt", line.receipt_line, receipt_work_item_id),
            ("invoice", line.invoice_line, invoice_work_item_id),
        ):
            pointer = _evidence_for(source, work_item_id=work_item_id)
            if pointer is not None:
                evidence[side] = pointer

        db.add(
            ProcurementCaseLine(
                organization_id=organization_id,
                workspace_id=workspace_id,
                case_id=case.id,
                line_number=line.line_number,
                outcome=line.outcome,
                description=line.description or None,
                sku=line.sku or None,
                po_line_index=line.po_line.index if line.po_line else None,
                po_quantity=line.po_line.quantity if line.po_line else None,
                po_unit_price_micros=line.po_line.unit_price_micros if line.po_line else None,
                po_amount_micros=line.po_line.amount_micros if line.po_line else None,
                receipt_line_index=line.receipt_line.index if line.receipt_line else None,
                receipt_quantity=line.receipt_line.quantity if line.receipt_line else None,
                invoice_line_index=line.invoice_line.index if line.invoice_line else None,
                invoice_quantity=line.invoice_line.quantity if line.invoice_line else None,
                invoice_unit_price_micros=(
                    line.invoice_line.unit_price_micros if line.invoice_line else None
                ),
                invoice_amount_micros=(
                    line.invoice_line.amount_micros if line.invoice_line else None
                ),
                pair_cost=line.pair_cost,
                price_delta_micros=line.price_delta_micros,
                quantity_delta=line.quantity_delta,
                findings=list(line.findings),
                evidence=evidence,
            )
        )
    db.flush()
    return case


def _meter_once(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    case: ProcurementCase,
    digest: str,
) -> None:
    """One usage event per input_digest. See the module header on the key."""
    usage_service.record_usage(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        event_type=USAGE_EVENT_PROCUREMENT_CASE,
        quantity=1,
        resource_type="PROCUREMENT_CASE",
        resource_id=case.id,
        idempotency_key=f"{USAGE_EVENT_PROCUREMENT_CASE}:{digest}",
        details={"policy_version": case.policy_version, "line_count": case.line_count},
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _load_live(db: Session, *, workspace_id: uuid.UUID, case_id: uuid.UUID) -> ProcurementCase:
    case = db.execute(
        select(ProcurementCase)
        .options(selectinload(ProcurementCase.lines))
        .where(
            ProcurementCase.id == case_id,
            ProcurementCase.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if case is None:
        raise CaseNotLive("no such case in this workspace")
    if case.status == CASE_STATUS_SUPERSEDED:
        raise CaseNotLive(
            "this case has been superseded by a re-score; open the current "
            "case for this invoice instead"
        )
    if case.status in RESOLVED_CASE_STATUSES:
        raise CaseNotLive(f"this case was already {case.status.lower()}")
    return case


def approve_case(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
    override_reason: Optional[str] = None,
) -> ProcurementCase:
    """Approve. A case with red lines requires a written override reason.

    The rule is here and in the route's response model and in the UI. Three
    places is not redundancy: the UI stops an honest mistake, the schema
    stops a malformed client, and this stops the API being driven directly.
    Only this one is load-bearing for the audit trail.
    """
    case = _load_live(db, workspace_id=workspace_id, case_id=case_id)

    red_lines = [line for line in case.lines if line.outcome in RED_OUTCOMES]
    reason = (override_reason or "").strip()
    if red_lines and not reason:
        raise OverrideReasonRequired(
            f"this case has {len(red_lines)} line(s) that did not match. "
            f"Approving it anyway requires a written reason, which is what "
            f"an auditor reads when asking why this invoice was paid despite "
            f"the exception."
        )

    case.status = CASE_STATUS_APPROVED
    case.resolved_at = datetime.now(timezone.utc)
    case.resolved_by_user_id = actor_id
    case.resolution_reason = reason or None
    db.flush([case])

    audit_service.record(
        db,
        organization_id=case.organization_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.PROCUREMENT_CASE,
        resource_id=case.id,
        action=AuditAction.MATCH_APPROVED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "exception_count": case.exception_count,
            "variance_micros": case.variance_micros,
            "policy_version": case.policy_version,
            "override_given": bool(reason),
            "red_outcomes": sorted({line.outcome for line in red_lines}),
        },
    )
    outbox_service.emit_public_with_twin(
        db,
        organization_id=case.organization_id,
        workspace_id=workspace_id,
        event_type=EVENT_APPROVED,
        resource_id=case.id,
        twin_resource_id=case.invoice_work_item_id,
        idempotency_key=f"{EVENT_APPROVED}:{case.id}",
        payload={
            "case_id": str(case.id),
            "exception_count": case.exception_count,
            "variance_micros": case.variance_micros,
            "policy_version": case.policy_version,
        },
    )
    return case


def dispute_case(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
    reason: str,
) -> ProcurementCase:
    case = _load_live(db, workspace_id=workspace_id, case_id=case_id)

    text = (reason or "").strip()
    if len(text) < MINIMUM_DISPUTE_REASON:
        raise CaseServiceError(
            f"a dispute reason must be at least {MINIMUM_DISPUTE_REASON} "
            f"characters. This text is what goes to the supplier; 'wrong' "
            f"starts a phone call rather than ending one."
        )

    case.status = CASE_STATUS_DISPUTED
    case.resolved_at = datetime.now(timezone.utc)
    case.resolved_by_user_id = actor_id
    case.resolution_reason = text
    db.flush([case])

    audit_service.record(
        db,
        organization_id=case.organization_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.PROCUREMENT_CASE,
        resource_id=case.id,
        action=AuditAction.MATCH_DISPUTED,
        details={
            "exception_count": case.exception_count,
            "variance_micros": case.variance_micros,
            "policy_version": case.policy_version,
        },
    )
    outbox_service.emit_public_with_twin(
        db,
        organization_id=case.organization_id,
        workspace_id=workspace_id,
        event_type=EVENT_DISPUTED,
        resource_id=case.id,
        twin_resource_id=case.invoice_work_item_id,
        idempotency_key=f"{EVENT_DISPUTED}:{case.id}",
        payload={
            "case_id": str(case.id),
            "reason": text,
            "variance_micros": case.variance_micros,
        },
    )
    return case


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_case(
    db: Session, *, workspace_id: uuid.UUID, case_id: uuid.UUID
) -> Optional[ProcurementCase]:
    return db.execute(
        select(ProcurementCase)
        .options(selectinload(ProcurementCase.lines))
        .where(
            ProcurementCase.id == case_id,
            ProcurementCase.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()


def list_cases(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    statuses: Optional[Sequence[str]] = None,
    min_variance_micros: Optional[int] = None,
    has_exceptions: Optional[bool] = None,
    created_after: Optional[datetime] = None,
    created_before: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ProcurementCase]:
    statement = select(ProcurementCase).where(
        ProcurementCase.workspace_id == workspace_id
    )
    if statuses:
        statement = statement.where(ProcurementCase.status.in_(list(statuses)))
    else:
        # SUPERSEDED is excluded by default. It is history, and a queue that
        # shows every superseded version of an invoice grows without bound
        # and buries the one row that needs attention.
        statement = statement.where(ProcurementCase.status != CASE_STATUS_SUPERSEDED)
    if min_variance_micros is not None:
        from sqlalchemy import func

        statement = statement.where(
            func.abs(ProcurementCase.variance_micros) >= int(min_variance_micros)
        )
    if has_exceptions is True:
        statement = statement.where(ProcurementCase.exception_count > 0)
    elif has_exceptions is False:
        statement = statement.where(ProcurementCase.exception_count == 0)
    if created_after is not None:
        statement = statement.where(ProcurementCase.created_at >= created_after)
    if created_before is not None:
        statement = statement.where(ProcurementCase.created_at <= created_before)

    statement = statement.order_by(
        ProcurementCase.created_at.desc(), ProcurementCase.id.desc()
    ).limit(min(int(limit), 200)).offset(max(int(offset), 0))
    return list(db.execute(statement).scalars().all())


# ---------------------------------------------------------------------------
# Tolerance policies
# ---------------------------------------------------------------------------


def publish_policy(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
    price_tolerance_micros: int,
    price_tolerance_bps: int,
    quantity_tolerance: Decimal,
    max_pair_cost: int,
    candidate_window_days: int,
) -> ProcurementTolerancePolicy:
    """Publish the next version. Never edits a published row — the trigger refuses.

    Publishing does NOT re-score anything. The next sweep will: every open
    case's digest now disagrees with the new policy version, so each is
    superseded and re-scored on its own schedule. Re-scoring the estate
    inline would hold a transaction across every case in the workspace.
    """
    current_max = db.execute(
        select(ProcurementTolerancePolicy.version)
        .where(ProcurementTolerancePolicy.workspace_id == workspace_id)
        .order_by(ProcurementTolerancePolicy.version.desc())
        .limit(1)
    ).scalar_one_or_none()

    policy = ProcurementTolerancePolicy(
        organization_id=organization_id,
        workspace_id=workspace_id,
        version=(int(current_max) + 1) if current_max else 1,
        status="PUBLISHED",
        price_tolerance_micros=int(price_tolerance_micros),
        price_tolerance_bps=int(price_tolerance_bps),
        quantity_tolerance=Decimal(quantity_tolerance),
        max_pair_cost=int(max_pair_cost),
        candidate_window_days=int(candidate_window_days),
        published_at=datetime.now(timezone.utc),
        published_by_user_id=actor_id,
        created_by_user_id=actor_id,
    )
    db.add(policy)
    db.flush([policy])

    audit_service.record(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.PROCUREMENT_TOLERANCE_POLICY,
        resource_id=policy.id,
        action=AuditAction.TOLERANCE_PUBLISHED,
        details={
            "version": policy.version,
            "price_tolerance_micros": policy.price_tolerance_micros,
            "price_tolerance_bps": policy.price_tolerance_bps,
            "quantity_tolerance": format(Decimal(policy.quantity_tolerance), "f"),
            "max_pair_cost": policy.max_pair_cost,
        },
    )
    return policy


def impact_preview(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    price_tolerance_micros: int,
    price_tolerance_bps: int,
    quantity_tolerance: Decimal,
    window_days: int = 30,
) -> dict[str, Any]:
    """What the last `window_days` of cases would have looked like under this draft.

    Re-classifies the STORED deltas rather than re-running the matcher. That
    is deliberate and it is a real limitation, stated in the response: a
    tolerance change cannot alter which lines paired, only whether a paired
    line's delta counts as a variance, so re-pairing would be wasted work.
    A change to `max_pair_cost` WOULD alter pairing, which is why it is not
    an input here and the response says so.
    """
    from datetime import timedelta

    since = datetime.now(timezone.utc) - timedelta(days=int(window_days))
    cases = db.execute(
        select(ProcurementCase)
        .options(selectinload(ProcurementCase.lines))
        .where(
            ProcurementCase.workspace_id == workspace_id,
            ProcurementCase.created_at >= since,
            ProcurementCase.status != CASE_STATUS_SUPERSEDED,
        )
    ).scalars().all()

    draft = policy_module.TolerancePolicy(
        policy_version="draft:preview",
        price_tolerance_micros=int(price_tolerance_micros),
        price_tolerance_bps=int(price_tolerance_bps),
        quantity_tolerance=Decimal(quantity_tolerance),
    )

    before_exceptions = 0
    after_exceptions = 0
    newly_clean = 0
    newly_flagged = 0

    for case in cases:
        case_after = 0
        for line in case.lines:
            was_red = line.outcome in RED_OUTCOMES
            if was_red:
                before_exceptions += 1

            # Presence outcomes are unaffected by tolerance: no amount of
            # slack conjures a purchase order line that does not exist.
            if line.outcome in ("NOT_ORDERED", "NOT_INVOICED", "NOT_RECEIVED"):
                case_after += 1
                after_exceptions += 1
                continue

            price_ok = True
            if line.price_delta_micros is not None and line.po_unit_price_micros is not None:
                price_ok = policy_module.within_price_tolerance(
                    line.po_unit_price_micros,
                    int(line.po_unit_price_micros) + int(line.price_delta_micros),
                    draft,
                )
            quantity_ok = True
            if line.quantity_delta is not None:
                quantity_ok = abs(Decimal(line.quantity_delta)) <= Decimal(
                    draft.quantity_tolerance
                )

            is_red = not (price_ok and quantity_ok)
            if is_red:
                case_after += 1
                after_exceptions += 1
            if was_red and not is_red:
                newly_clean += 1
            if is_red and not was_red:
                newly_flagged += 1

    return {
        "window_days": int(window_days),
        "cases_considered": len(cases),
        "exceptions_today": before_exceptions,
        "exceptions_under_draft": after_exceptions,
        "lines_that_would_clear": newly_clean,
        "lines_that_would_flag": newly_flagged,
        "caveat": (
            "This preview re-classifies the variances already recorded on "
            "these cases. It cannot show the effect of a change to "
            "max_pair_cost, which alters which lines pair with which and "
            "therefore requires a full re-score."
        ),
    }