"""ARCH-35 — the per-score autonomy decision, given a model snapshot.

`apply.calibrated` loads a snapshot from `calibration_models` and hands it
here. Everything that decides whether a score may be acted on without a human
is in `decide()`, in this order, and the order is the policy:

    no model / PRIOR / stale   -> no probability at all (cold start)
    SUSPENDED                  -> probability, but never automatic
    promise not achievable     -> probability, but never automatic
    p < λ                      -> probability, review
    audit sample               -> probability, review, labelled as an audit
    otherwise                  -> automatic

WHY THIS IS SEPARATE FROM `apply.py`
====================================

`apply.py` holds a Session, and a Session cannot be gated offline. The one
mutation this module must never survive — "the suspension check skipped" —
is a single deleted `if`, and a single deleted `if` can only be caught by a
gate that runs without a database. So the decision is here, pure, and
`verify_arch35.py` drives it directly.

PURE
====

`now` is an argument. No Session, no clock, no random source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Optional

from app.services.calibration import estimators, monitor, sampling
from app.services.calibration import vocabulary as vocab

__all__ = [
    "ModelSnapshot",
    "AutonomyDecision",
    "AutonomyTerms",
    "decide",
    "terms_of",
    "PROBABILITY_PLACES",
]

#: `assertion_evaluations.calibrated_probability numeric(6,5)`.
PROBABILITY_PLACES: Decimal = Decimal("0.00001")

REASON_NO_MODEL = "no_model"
REASON_PRIOR = "cold_start"
REASON_STALE = "stale"
REASON_SUSPENDED = "suspended"
REASON_NOT_ACHIEVABLE = "not_achievable"
REASON_BELOW_THRESHOLD = "below_threshold"
REASON_AUDIT = "audit_sample"
REASON_AUTOMATIC = "automatic"


@dataclass(frozen=True)
class ModelSnapshot:
    """The fields of one `calibration_models` row a decision needs."""

    model_id: str
    decision_type: str
    method: str
    parameters: Mapping[str, Any]
    status: str
    threshold: Decimal
    target_error_rate: Decimal
    conformal_bound: Decimal
    clopper_pearson_upper: Decimal
    auto_share: Decimal
    audit_sample_rate: Decimal
    label_count: int
    last_checked_at: Optional[datetime]
    suspended_reason: Optional[str] = None

    @property
    def achievable(self) -> bool:
        """The stored promise is backed: something passes, within α."""
        return (
            self.auto_share > 0
            and self.threshold < 1
            and self.conformal_bound <= self.target_error_rate
        )


@dataclass(frozen=True)
class AutonomyTerms:
    """What ARCH-33's `CalibrationModel` carries about a stored model."""

    model_id: str
    status: str
    threshold: Decimal
    audit_sample_rate: Decimal
    achievable: bool
    suspended_reason: Optional[str] = None

    @property
    def allows_automation(self) -> bool:
        return self.status == vocab.STATUS_ACTIVE and self.achievable


@dataclass(frozen=True)
class AutonomyDecision:
    probability: Optional[Decimal]
    model_id: Optional[str]
    auto_allowed: bool
    reason: str
    #: The platform WOULD have approved this automatically, and an audit
    #: sample sent it to review instead.
    audit_sample: bool = False
    threshold: Optional[Decimal] = None
    audit_sample_rate: Optional[Decimal] = None
    explanation: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def would_auto_approve(self) -> bool:
        return self.auto_allowed or self.audit_sample

    def as_details(self) -> dict[str, Any]:
        return {
            "probability": None if self.probability is None else str(self.probability),
            "model_id": self.model_id,
            "auto_allowed": self.auto_allowed,
            "reason": self.reason,
            "audit_sample": self.audit_sample,
            "would_auto_approve": self.would_auto_approve,
            "threshold": None if self.threshold is None else str(self.threshold),
            "audit_sample_rate": (
                None if self.audit_sample_rate is None else str(self.audit_sample_rate)
            ),
            "explanation": self.explanation,
        }


def _quantize(value: float) -> Decimal:
    clamped = estimators.clamp(value)
    return Decimal(repr(clamped)).quantize(PROBABILITY_PLACES, rounding=ROUND_HALF_UP)


def terms_of(snapshot: ModelSnapshot) -> AutonomyTerms:
    return AutonomyTerms(
        model_id=snapshot.model_id,
        status=snapshot.status,
        threshold=snapshot.threshold,
        audit_sample_rate=snapshot.audit_sample_rate,
        achievable=snapshot.achievable,
        suspended_reason=snapshot.suspended_reason,
    )


def decide(
    snapshot: Optional[ModelSnapshot],
    raw_score: Any,
    *,
    sample_key: Any,
    now: datetime,
) -> AutonomyDecision:
    """Apply the policy in the module header to one score."""
    if snapshot is None:
        return AutonomyDecision(
            probability=None,
            model_id=None,
            auto_allowed=False,
            reason=REASON_NO_MODEL,
            explanation=(
                "No calibration has been fitted for this decision yet, so it "
                "goes to review."
            ),
        )

    if snapshot.method == vocab.METHOD_PRIOR:
        return AutonomyDecision(
            probability=None,
            model_id=snapshot.model_id,
            auto_allowed=False,
            reason=REASON_PRIOR,
            explanation=(
                "Not enough reviewed documents yet to estimate accuracy; "
                "everything goes to review until "
                f"{vocab.MIN_LABELS_PLATT} are reviewed "
                f"({snapshot.label_count} so far)."
            ),
        )

    if monitor.is_stale(snapshot.last_checked_at, now):
        return AutonomyDecision(
            probability=None,
            model_id=snapshot.model_id,
            auto_allowed=False,
            reason=REASON_STALE,
            explanation=(
                "The accuracy check for this decision has not run recently, "
                "so no limit can be vouched for and it goes to review."
            ),
        )

    value = estimators.evaluate(snapshot.method, snapshot.parameters, raw_score)
    probability = None if value is None else _quantize(value)
    common = {
        "probability": probability,
        "model_id": snapshot.model_id,
        "threshold": snapshot.threshold,
        "audit_sample_rate": snapshot.audit_sample_rate,
    }

    if snapshot.status == vocab.STATUS_SUSPENDED:
        return AutonomyDecision(
            **common,
            auto_allowed=False,
            reason=REASON_SUSPENDED,
            explanation=snapshot.suspended_reason
            or "Automatic approval is paused for this decision.",
        )

    if snapshot.status != vocab.STATUS_ACTIVE or not snapshot.achievable:
        return AutonomyDecision(
            **common,
            auto_allowed=False,
            reason=REASON_NOT_ACHIEVABLE,
            explanation=(
                "The chosen error limit is not achievable on the reviewed "
                "documents so far, so this goes to review."
            ),
        )

    if probability is None or probability < snapshot.threshold:
        return AutonomyDecision(
            **common,
            auto_allowed=False,
            reason=REASON_BELOW_THRESHOLD,
            explanation=(
                f"Calibrated confidence {probability} is below the "
                f"{snapshot.threshold} the error limit requires."
            ),
        )

    if sampling.is_audit_sample(
        snapshot.model_id, sample_key, snapshot.audit_sample_rate
    ):
        return AutonomyDecision(
            **common,
            auto_allowed=False,
            reason=REASON_AUDIT,
            audit_sample=True,
            explanation=(
                "This would have been approved automatically. It was picked "
                "at random for an accuracy audit, so a person checks it."
            ),
        )

    return AutonomyDecision(
        **common,
        auto_allowed=True,
        reason=REASON_AUTOMATIC,
        explanation=(
            f"Calibrated confidence {probability} is at or above "
            f"{snapshot.threshold}; approved within the chosen error limit."
        ),
    )
