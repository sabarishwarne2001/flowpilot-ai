"""ARCH-35 §6.6 — harvesting reviewer outcomes into `calibration_labels`.

Every place a person confirms or overturns what the platform said is a label:
the platform's score at decision time, and whether the person agreed.

    document_verifications         verification.document   (all fields reviewed)
    document_verification_fields   verification.field      (each resolved field)
    assertion_evaluations          assertion.<family>      (reviewer_verdict set)
    procurement_cases              reconciliation.case     (APPROVED / DISPUTED)
    anomaly_findings               anomaly.finding         (CONFIRMED / DISMISSED)

IDEMPOTENT ON `uq_cl_source`
============================

`INSERT ... ON CONFLICT (source_table, source_id, decision_type) DO NOTHING`.
The hourly job re-reads a 48-hour overlap every run; a row already harvested
writes nothing. A label is immutable once written: a review, once recorded,
is what happened.

A DOCUMENT-LEVEL LABEL NEEDS EVERY FIELD TO HAVE BEEN LOOKED AT
==============================================================

ARCH-13's legacy review shows only the fields the agents disagreed on. If
three of ten fields were reviewed and all three matched the consensus, that
does not say the other seven were right — nobody looked. So
`verification.document` is labelled only when EVERY non-assertion field on
the verification carries a resolved value. ARCH-35's own reviews always
qualify, because a verification held back by calibrated autonomy is marked
`review_all_fields` and the reviewer confirms every field.

AUDITS CARRY THEIR WEIGHT
=========================

An audit review is one of the 1/r automatic passes it was sampled from. The
decision recorded at the time says which reviews were audits and at which
rate; the label carries weight 1/r. See `risk.py`.

THIS MODULE HOLDS THE SESSION
=============================

The mapping from a review to a label is simple enough to read in place, and
every branch of it reads ORM rows. `fit.examples_from_labels` is the pure half.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app.models.assertion import AssertionDefinition, AssertionEvaluation
from app.models.calibration import CalibrationLabel
from app.models.procurement import ProcurementCase
from app.models.radar import AnomalyFinding
from app.models.verification import (
    DocumentVerification,
    DocumentVerificationField,
    VerificationStatus,
)
from app.services.calibration import sampling
from app.services.calibration import vocabulary as vocab

logger = logging.getLogger("app.services.calibration.labels")

__all__ = [
    "HarvestResult",
    "harvest",
    "recent_scores",
    "realized_since",
    "label_count",
    "labels_for_fit",
    "assertion_sample_key",
    "HARVEST_OVERLAP",
    "BATCH_LIMIT",
]

#: Re-read window on every incremental run.
HARVEST_OVERLAP: timedelta = timedelta(hours=48)

#: Rows per source per run.
BATCH_LIMIT: int = 5000

_SCORE_PLACES = Decimal("0.0000001")


@dataclass
class HarvestResult:
    organization_id: uuid.UUID
    backfill: bool
    offered: dict[str, int] = field(default_factory=dict)
    inserted: dict[str, int] = field(default_factory=dict)

    @property
    def total_inserted(self) -> int:
        return sum(self.inserted.values())

    def as_payload(self) -> dict[str, Any]:
        return {
            "organization_id": str(self.organization_id),
            "backfill": self.backfill,
            "offered": dict(self.offered),
            "inserted": dict(self.inserted),
        }


def assertion_sample_key(
    definition_id: Any, work_item_id: Any, node_run_id: Any
) -> str:
    """The stable key ARCH-35 samples an assertion's audit on.

    The evaluation row's id does not exist when the decision is made, so the
    key is the three ids that do — and that the evaluation row records.
    """
    return f"{definition_id}:{work_item_id}:{node_run_id}"


def _score(value: Any) -> Decimal:
    number = Decimal(str(value))
    number = min(Decimal("1"), max(Decimal("0"), number))
    return number.quantize(_SCORE_PLACES, rounding=ROUND_HALF_UP)


def _weight(audit: bool, rate: Any) -> Decimal:
    if not audit or rate in (None, ""):
        return Decimal("1")
    return max(Decimal("1"), sampling.audit_weight(rate))


def _row(
    *,
    organization_id: uuid.UUID,
    decision_type: str,
    raw_score: Any,
    correct: bool,
    source_table: str,
    source_id: uuid.UUID,
    was_auto_approved: bool,
    was_audit_sample: bool,
    auto_eligible: bool,
    audit_rate: Any,
    observed_at: datetime,
) -> dict[str, Any]:
    audit = bool(was_audit_sample) and bool(was_auto_approved)
    return {
        "id": uuid.uuid4(),
        "organization_id": organization_id,
        "decision_type": decision_type,
        "raw_score": _score(raw_score),
        "correct": bool(correct),
        "source_table": source_table,
        "source_id": source_id,
        "was_auto_approved": bool(was_auto_approved),
        "was_audit_sample": audit,
        "auto_eligible": bool(auto_eligible),
        "sample_weight": _weight(audit, audit_rate),
        "observed_at": observed_at,
    }


def _calibration_details(details: Any) -> dict[str, Any]:
    if not isinstance(details, dict):
        return {}
    value = details.get("calibration")
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def _from_verifications(
    db: Session, organization_id: uuid.UUID, since: Optional[datetime]
) -> list[dict[str, Any]]:
    from app.services.document_verification_service import loose_equal

    stmt = (
        select(DocumentVerification)
        .options(selectinload(DocumentVerification.fields))
        .where(
            DocumentVerification.organization_id == organization_id,
            DocumentVerification.status == VerificationStatus.REVIEWED,
            DocumentVerification.reviewed_at.isnot(None),
        )
        .order_by(DocumentVerification.reviewed_at.asc())
        .limit(BATCH_LIMIT)
    )
    if since is not None:
        stmt = stmt.where(DocumentVerification.reviewed_at >= since)

    rows: list[dict[str, Any]] = []
    for verification in db.execute(stmt).scalars().all():
        calibration = _calibration_details(verification.details)
        would = bool(calibration.get("would_auto_approve"))
        audit = bool(calibration.get("audit_sample")) and would
        rate = calibration.get("audit_sample_rate")
        fields = [
            f
            for f in verification.fields
            if not f.field_path.startswith(vocab.ASSERTION_FIELD_PATH_PREFIX)
        ]
        observed = verification.reviewed_at

        for f in fields:
            if f.resolved_value is None:
                continue
            rows.append(
                _row(
                    organization_id=organization_id,
                    decision_type=vocab.DECISION_VERIFICATION_FIELD,
                    raw_score=f.confidence,
                    correct=loose_equal(f.resolved_value, f.consensus_value),
                    source_table=vocab.SOURCE_VERIFICATION_FIELDS,
                    source_id=f.id,
                    was_auto_approved=would,
                    was_audit_sample=audit,
                    auto_eligible=False,
                    audit_rate=rate,
                    observed_at=observed,
                )
            )

        if (
            verification.confidence is None
            or not fields
            or any(f.resolved_value is None for f in fields)
        ):
            continue
        rows.append(
            _row(
                organization_id=organization_id,
                decision_type=vocab.DECISION_VERIFICATION_DOCUMENT,
                raw_score=verification.confidence,
                correct=all(
                    loose_equal(f.resolved_value, f.consensus_value) for f in fields
                ),
                source_table=vocab.SOURCE_DOCUMENT_VERIFICATIONS,
                source_id=verification.id,
                was_auto_approved=would,
                was_audit_sample=audit,
                auto_eligible=True,
                audit_rate=rate,
                observed_at=observed,
            )
        )
    return rows


def _from_assertions(
    db: Session, organization_id: uuid.UUID, since: Optional[datetime]
) -> list[dict[str, Any]]:
    stmt = (
        select(AssertionEvaluation, AssertionDefinition.family, DocumentVerification.details)
        .join(
            AssertionDefinition,
            AssertionDefinition.id == AssertionEvaluation.definition_id,
        )
        .outerjoin(
            DocumentVerification,
            DocumentVerification.id == AssertionEvaluation.verification_id,
        )
        .where(
            AssertionEvaluation.organization_id == organization_id,
            AssertionEvaluation.reviewer_verdict.isnot(None),
            AssertionEvaluation.reviewed_at.isnot(None),
        )
        .order_by(AssertionEvaluation.reviewed_at.asc())
        .limit(BATCH_LIMIT)
    )
    if since is not None:
        stmt = stmt.where(AssertionEvaluation.reviewed_at >= since)

    rows: list[dict[str, Any]] = []
    for evaluation, family, details in db.execute(stmt).all():
        if family not in vocab.ASSERTION_FAMILIES:
            continue
        audits = _calibration_details(details).get("assertion_audits") or {}
        key = assertion_sample_key(
            evaluation.definition_id, evaluation.work_item_id, evaluation.node_run_id
        )
        audited = isinstance(audits, dict) and key in audits
        rows.append(
            _row(
                organization_id=organization_id,
                decision_type=vocab.assertion_decision_type(family),
                raw_score=evaluation.raw_score,
                # Engine correctness. A confirmed FAIL is a CORRECT example.
                correct=(evaluation.reviewer_verdict == evaluation.verdict),
                source_table=vocab.SOURCE_ASSERTION_EVALUATIONS,
                source_id=evaluation.id,
                was_auto_approved=audited,
                was_audit_sample=audited,
                auto_eligible=(evaluation.verdict == "PASS"),
                audit_rate=audits.get(key) if audited else None,
                observed_at=evaluation.reviewed_at,
            )
        )
    return rows


def _case_is_clean(case: ProcurementCase) -> bool:
    self_inconsistent = any(
        isinstance(finding, dict) and finding.get("code") == "SELF_INCONSISTENT_TOTAL"
        for finding in (case.header_findings or [])
    )
    return case.exception_count == 0 and not self_inconsistent


def _from_procurement(
    db: Session, organization_id: uuid.UUID, since: Optional[datetime]
) -> list[dict[str, Any]]:
    stmt = (
        select(ProcurementCase)
        .where(
            ProcurementCase.organization_id == organization_id,
            ProcurementCase.status.in_(("APPROVED", "DISPUTED")),
            ProcurementCase.resolved_at.isnot(None),
            ProcurementCase.resolved_by_user_id.isnot(None),
            ProcurementCase.line_count > 0,
        )
        .order_by(ProcurementCase.resolved_at.asc())
        .limit(BATCH_LIMIT)
    )
    if since is not None:
        stmt = stmt.where(ProcurementCase.resolved_at >= since)

    rows: list[dict[str, Any]] = []
    for case in db.execute(stmt).scalars().all():
        clean = _case_is_clean(case)
        matched_share = Decimal(case.line_count - case.exception_count) / Decimal(
            case.line_count
        )
        rows.append(
            _row(
                organization_id=organization_id,
                decision_type=vocab.DECISION_RECONCILIATION_CASE,
                raw_score=matched_share,
                # The matcher's claim was "clean" or "not clean"; the person
                # approving or disputing the case is the verdict on it.
                correct=(clean == (case.status == "APPROVED")),
                source_table=vocab.SOURCE_PROCUREMENT_CASES,
                source_id=case.id,
                was_auto_approved=False,
                was_audit_sample=False,
                auto_eligible=clean,
                audit_rate=None,
                observed_at=case.resolved_at,
            )
        )
    return rows


def _from_anomalies(
    db: Session, organization_id: uuid.UUID, since: Optional[datetime]
) -> list[dict[str, Any]]:
    stmt = (
        select(AnomalyFinding)
        .where(
            AnomalyFinding.organization_id == organization_id,
            AnomalyFinding.status.in_(("CONFIRMED", "DISMISSED")),
            AnomalyFinding.resolved_at.isnot(None),
        )
        .order_by(AnomalyFinding.resolved_at.asc())
        .limit(BATCH_LIMIT)
    )
    if since is not None:
        stmt = stmt.where(AnomalyFinding.resolved_at >= since)

    return [
        _row(
            organization_id=organization_id,
            decision_type=vocab.DECISION_ANOMALY_FINDING,
            raw_score=finding.score,
            correct=(finding.status == "CONFIRMED"),
            source_table=vocab.SOURCE_ANOMALY_FINDINGS,
            source_id=finding.id,
            was_auto_approved=False,
            was_audit_sample=False,
            auto_eligible=False,
            audit_rate=None,
            observed_at=finding.resolved_at,
        )
        for finding in db.execute(stmt).scalars().all()
    ]


_SOURCES = (
    ("verification", _from_verifications),
    ("assertion", _from_assertions),
    ("reconciliation", _from_procurement),
    ("anomaly", _from_anomalies),
)


def _insert(db: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    inserted = 0
    for start in range(0, len(rows), 500):
        chunk = rows[start:start + 500]
        result = db.execute(
            insert(CalibrationLabel)
            .values(chunk)
            .on_conflict_do_nothing(
                index_elements=["source_table", "source_id", "decision_type"]
            )
            .returning(CalibrationLabel.id)
        )
        inserted += len(result.scalars().all())
    return inserted


def harvest(
    db: Session,
    *,
    organization_id: uuid.UUID,
    now: Optional[datetime] = None,
    full: bool = False,
) -> HarvestResult:
    """Harvest every source for one organization. Flushes, never commits."""
    now = now or datetime.now(timezone.utc)
    existing = db.execute(
        select(func.count())
        .select_from(CalibrationLabel)
        .where(CalibrationLabel.organization_id == organization_id)
    ).scalar_one()
    backfill = full or existing == 0
    since = None if backfill else now - HARVEST_OVERLAP

    result = HarvestResult(organization_id=organization_id, backfill=backfill)
    for name, source in _SOURCES:
        rows = source(db, organization_id, since)
        result.offered[name] = len(rows)
        result.inserted[name] = _insert(db, rows)
    db.flush()
    logger.info("calibration.harvest", extra=result.as_payload())
    return result


# ---------------------------------------------------------------------------
# Reads the fit and the monitor need
# ---------------------------------------------------------------------------


def label_count(
    db: Session, *, organization_id: uuid.UUID, decision_type: str
) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(CalibrationLabel)
            .where(
                CalibrationLabel.organization_id == organization_id,
                CalibrationLabel.decision_type == decision_type,
            )
        ).scalar_one()
    )


def labels_for_fit(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision_type: str,
    limit: int = vocab.MAX_LABELS_PER_FIT,
) -> list[CalibrationLabel]:
    return list(
        db.execute(
            select(CalibrationLabel)
            .where(
                CalibrationLabel.organization_id == organization_id,
                CalibrationLabel.decision_type == decision_type,
            )
            .order_by(CalibrationLabel.observed_at.desc(), CalibrationLabel.id)
            .limit(int(limit))
        )
        .scalars()
        .all()
    )


def labels_since(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision_type: str,
    since: datetime,
) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(CalibrationLabel)
            .where(
                CalibrationLabel.organization_id == organization_id,
                CalibrationLabel.decision_type == decision_type,
                CalibrationLabel.created_at >= since,
            )
        ).scalar_one()
    )


def realized_since(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision_type: str,
    since: datetime,
    window: int = vocab.MONITOR_WINDOW,
) -> tuple[int, int]:
    """`(wrong, total)` over the last `window` reviewed automatic passes."""
    rows = db.execute(
        select(CalibrationLabel.correct)
        .where(
            CalibrationLabel.organization_id == organization_id,
            CalibrationLabel.decision_type == decision_type,
            CalibrationLabel.was_auto_approved.is_(True),
            CalibrationLabel.observed_at >= since,
        )
        .order_by(CalibrationLabel.observed_at.desc())
        .limit(int(window))
    ).scalars().all()
    return sum(1 for correct in rows if not correct), len(rows)


def recent_scores(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision_type: str,
    since: datetime,
    until: Optional[datetime] = None,
    limit: int = 20000,
) -> list[float]:
    """Production raw scores for one decision type — every decision, not
    only the reviewed ones. See `monitor.py` for why that matters."""
    until = until or datetime.now(timezone.utc)

    if decision_type == vocab.DECISION_VERIFICATION_DOCUMENT:
        stmt = select(DocumentVerification.confidence).where(
            DocumentVerification.organization_id == organization_id,
            DocumentVerification.confidence.isnot(None),
            DocumentVerification.created_at >= since,
            DocumentVerification.created_at < until,
        )
    elif decision_type == vocab.DECISION_VERIFICATION_FIELD:
        stmt = (
            select(DocumentVerificationField.confidence)
            .join(
                DocumentVerification,
                DocumentVerification.id == DocumentVerificationField.verification_id,
            )
            .where(
                DocumentVerification.organization_id == organization_id,
                DocumentVerificationField.created_at >= since,
                DocumentVerificationField.created_at < until,
                ~DocumentVerificationField.field_path.startswith(
                    vocab.ASSERTION_FIELD_PATH_PREFIX
                ),
            )
        )
    elif decision_type == vocab.DECISION_RECONCILIATION_CASE:
        stmt = select(
            (
                (ProcurementCase.line_count - ProcurementCase.exception_count)
                * 1.0
                / ProcurementCase.line_count
            )
        ).where(
            ProcurementCase.organization_id == organization_id,
            ProcurementCase.line_count > 0,
            ProcurementCase.created_at >= since,
            ProcurementCase.created_at < until,
        )
    elif decision_type == vocab.DECISION_ANOMALY_FINDING:
        stmt = select(AnomalyFinding.score).where(
            AnomalyFinding.organization_id == organization_id,
            AnomalyFinding.created_at >= since,
            AnomalyFinding.created_at < until,
        )
    else:
        family = vocab.family_of(decision_type)
        if family is None:
            raise ValueError(f"{decision_type!r} is not a decision type")
        stmt = (
            select(AssertionEvaluation.raw_score)
            .join(
                AssertionDefinition,
                AssertionDefinition.id == AssertionEvaluation.definition_id,
            )
            .where(
                AssertionEvaluation.organization_id == organization_id,
                AssertionDefinition.family == family,
                AssertionEvaluation.created_at >= since,
                AssertionEvaluation.created_at < until,
            )
        )

    return [
        float(value)
        for value in db.execute(stmt.limit(int(limit))).scalars().all()
        if value is not None
    ]


def label_rows(rows: Iterable[CalibrationLabel]) -> list[CalibrationLabel]:
    return list(rows)
