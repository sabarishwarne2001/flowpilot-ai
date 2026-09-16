"""ARCH-35 §6.6 — `calibrated(org, decision_type, raw_score)`.

    calibrated(db, organization_id, decision_type, raw_score, *, sample_key)
        -> CalibratedScore(probability, model_id, auto_allowed)

The one function every automatic decision in the platform calls. It loads the
model in force for the pair, hands it to `decision.decide`, and returns the
answer. It never approves anything for a tenant without
`capability.calibrated_autonomy` — those tenants keep today's fixed
thresholds, and the two callers below route them there before asking.

THE TWO CALLERS
===============

  * `document_verification_service.triage` -> `decide_verification`, which
    backs `document_verifications.auto_approved`.
  * ARCH-33's `triage.record_evaluation` -> `assertion_model` (the stored
    model, in ARCH-33's `CalibrationModel` shape) and `withhold_assertion`
    (the audit and suspension demotion).

WHY ASSERTIONS DEMOTE AFTER ROUTING RATHER THAN BEFORE
======================================================

`routing.decide` is ARCH-33's single route to the `pass` edge and stays
untouched. `withhold_assertion` only ever turns a PASS route into a TRIAGE
route — never the reverse — so it cannot open a second path to the `pass`
edge. The demoted row keeps its calibrated probability, which
`ck_ae_routing_consistent` permits on a TRIAGE row, and `resolve_assertion`
accepts it because it is a TRIAGE row. That is what makes an audited
assertion reviewable at all.

THIS MODULE HOLDS THE SESSION. It never commits.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, NamedTuple, Optional

from sqlalchemy.orm import Session

from app.models.calibration import CalibrationModelVersion
from app.services.calibration import decision as decision_module
from app.services.calibration import estimators
from app.services.calibration import labels as labels_module
from app.services.calibration import monitor, refit
from app.services.calibration import vocabulary as vocab

logger = logging.getLogger("app.services.calibration.apply")

__all__ = [
    "CalibratedScore",
    "autonomy_enabled",
    "snapshot_of",
    "snapshot",
    "calibrated",
    "decide",
    "decide_verification",
    "assertion_model",
    "withhold_assertion",
    "record_assertion_audit",
]


class CalibratedScore(NamedTuple):
    probability: Optional[Decimal]
    model_id: Optional[str]
    auto_allowed: bool


def autonomy_enabled(db: Session, *, organization_id: uuid.UUID) -> bool:
    from app.api import capability_gate

    return capability_gate.has_capability(
        db,
        organization_id=organization_id,
        capability_key=vocab.CAPABILITY_CALIBRATED_AUTONOMY,
    )


def snapshot_of(row: CalibrationModelVersion) -> decision_module.ModelSnapshot:
    return decision_module.ModelSnapshot(
        model_id=str(row.id),
        decision_type=row.decision_type,
        method=row.method,
        parameters=dict(row.breakpoints or {}),
        status=row.status,
        threshold=Decimal(row.threshold),
        target_error_rate=Decimal(row.target_error_rate),
        conformal_bound=Decimal(row.conformal_bound),
        clopper_pearson_upper=Decimal(row.clopper_pearson_upper),
        auto_share=Decimal(row.auto_share),
        audit_sample_rate=Decimal(row.audit_sample_rate),
        label_count=int(row.label_count),
        last_checked_at=row.last_checked_at,
        suspended_reason=row.suspended_reason,
    )


def snapshot(
    db: Session, *, organization_id: uuid.UUID, decision_type: str
) -> Optional[decision_module.ModelSnapshot]:
    row = refit.live_model(
        db, organization_id=organization_id, decision_type=decision_type
    )
    return None if row is None else snapshot_of(row)


def decide(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision_type: str,
    raw_score: Any,
    sample_key: Any,
    now: Optional[datetime] = None,
    check_capability: bool = True,
) -> Optional[decision_module.AutonomyDecision]:
    """The full decision, or None when the tenant does not have the capability."""
    if decision_type not in vocab.AUTOMATED_DECISION_TYPES:
        raise ValueError(
            f"{decision_type!r} has no automatic decision behind it; it is "
            "measured, not acted on."
        )
    if check_capability and not autonomy_enabled(db, organization_id=organization_id):
        return None
    return decision_module.decide(
        snapshot(db, organization_id=organization_id, decision_type=decision_type),
        raw_score,
        sample_key=sample_key,
        now=now or datetime.now(timezone.utc),
    )


def calibrated(
    db: Session,
    organization_id: uuid.UUID,
    decision_type: str,
    raw_score: Any,
    *,
    sample_key: Any = None,
    now: Optional[datetime] = None,
) -> CalibratedScore:
    """`(probability, model_id, auto_allowed)` — §6.6's signature.

    Without the capability: `(None, None, False)`. This function never grants
    autonomy a tenant has not bought; the legacy fixed-threshold paths are the
    callers' own.
    """
    outcome = decide(
        db,
        organization_id=organization_id,
        decision_type=decision_type,
        raw_score=raw_score,
        sample_key=sample_key if sample_key is not None else uuid.uuid4().hex,
        now=now,
    )
    if outcome is None:
        return CalibratedScore(None, None, False)
    return CalibratedScore(outcome.probability, outcome.model_id, outcome.auto_allowed)


# ---------------------------------------------------------------------------
# Extraction verification
# ---------------------------------------------------------------------------


def decide_verification(
    db: Session,
    *,
    verification: Any,
    confidence: Any,
    now: Optional[datetime] = None,
) -> Optional[decision_module.AutonomyDecision]:
    """`document_verifications.auto_approved`, calibrated. None -> legacy path."""
    decision = decide(
        db,
        organization_id=verification.organization_id,
        decision_type=vocab.DECISION_VERIFICATION_DOCUMENT,
        raw_score=confidence,
        sample_key=str(verification.id),
        now=now,
    )
    if decision is not None:
        logger.info(
            "calibration.verification_decision",
            extra={
                "verification_id": str(verification.id),
                **decision.as_details(),
            },
        )
    return decision


# ---------------------------------------------------------------------------
# ARCH-33 assertions
# ---------------------------------------------------------------------------


def assertion_model(
    db: Session,
    *,
    organization_id: uuid.UUID,
    family: str,
    now: Optional[datetime] = None,
) -> Any:
    """ARCH-33's `CalibrationModel`, built from the stored model in force.

    None when the tenant does not have the capability: ARCH-33's own per-call
    fit then applies, exactly as before this phase. With the capability, the
    stored model is the only source — a missing, PRIOR or stale model is an
    UNFITTED model, and ARCH-33's cold start takes it from there.
    """
    from app.services.assertions import calibration as assertion_calibration

    if not autonomy_enabled(db, organization_id=organization_id):
        return None

    now = now or datetime.now(timezone.utc)
    decision_type = vocab.assertion_decision_type(family)
    row = refit.live_model(
        db, organization_id=organization_id, decision_type=decision_type
    )
    rows = labels_module.labels_for_fit(
        db, organization_id=organization_id, decision_type=decision_type, limit=2000
    )
    examples = tuple(
        assertion_calibration.LabeledExample(
            raw_score=Decimal(str(label.raw_score)), correct=bool(label.correct)
        )
        for label in rows
    )

    if (
        row is None
        or row.method == vocab.METHOD_PRIOR
        or monitor.is_stale(row.last_checked_at, now)
    ):
        return assertion_calibration.CalibrationModel(
            family=family,
            points=(),
            label_count=len(examples) if row is None else int(row.label_count),
            fitted=False,
            model_id=None,
            examples=examples,
        )

    shot = snapshot_of(row)
    fitted = estimators.FittedMap(method=row.method, parameters=dict(row.breakpoints))
    points = tuple(
        assertion_calibration.CalibrationPoint(
            score=Decimal(repr(score)).quantize(assertion_calibration.PROBABILITY_PLACES),
            probability=Decimal(repr(probability)).quantize(
                assertion_calibration.PROBABILITY_PLACES
            ),
        )
        for score, probability in estimators.sample_curve(fitted)
    )
    return assertion_calibration.CalibrationModel(
        family=family,
        points=points,
        label_count=int(row.label_count),
        fitted=True,
        model_id=str(row.id),
        examples=examples,
        method=row.method,
        parameters=dict(row.breakpoints),
        autonomy=decision_module.terms_of(shot),
    )


def withhold_assertion(
    routing_decision: Any,
    *,
    model: Any,
    sample_key: str,
) -> tuple[Any, Optional[Decimal]]:
    """Demote a PASS route when the stored model says "not automatically".

    Returns `(decision, audit_rate)`; `audit_rate` is set only when the demotion
    was an audit sample. Never promotes.
    """
    from app.services.assertions import vocabulary as assertion_vocab
    from app.services.calibration import sampling

    terms = getattr(model, "autonomy", None)
    if terms is None or not routing_decision.continues:
        return routing_decision, None

    def _demote(reason: str) -> Any:
        return dataclasses.replace(
            routing_decision,
            routed_to=assertion_vocab.ROUTE_TRIAGE,
            edge=assertion_vocab.EDGE_FOR_ROUTE[assertion_vocab.ROUTE_TRIAGE],
            reason=reason,
        )

    if terms.status == vocab.STATUS_SUSPENDED:
        return (
            _demote(
                terms.suspended_reason
                or "Automatic approval is paused for this check, so it goes to review."
            ),
            None,
        )
    if not terms.allows_automation:
        return (
            _demote(
                "The chosen error limit is not achievable on the reviewed "
                "documents so far, so this goes to review."
            ),
            None,
        )
    if sampling.is_audit_sample(terms.model_id, sample_key, terms.audit_sample_rate):
        return (
            _demote(
                "The check passed and would have continued automatically. It "
                "was picked at random for an accuracy audit, so a person "
                "checks it."
            ),
            Decimal(terms.audit_sample_rate),
        )
    return routing_decision, None


def record_assertion_audit(
    verification: Any, *, sample_key: str, audit_rate: Decimal
) -> None:
    """Mark an audit on the verification, where the harvester will find it."""
    details = dict(verification.details or {})
    calibration = dict(details.get("calibration") or {})
    audits = dict(calibration.get("assertion_audits") or {})
    audits[sample_key] = str(audit_rate)
    calibration["assertion_audits"] = audits
    details["calibration"] = calibration
    verification.details = details
