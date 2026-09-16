"""ARCH-35 — request and response shapes for the Autonomy settings API.

Every rate, bound and threshold is a `Decimal` and serialises as a string, for
the reason ARCH-33 and ARCH-34 record: `0.05` has no exact binary float, and a
console that received `0.049999999999999996` would render a limit the tenant
did not choose.

The curves inside `AutonomyReliability` are floats on purpose. They are chart
coordinates, derived once at fit time and never compared against a stored
threshold by the client; the one comparison the client does make — "the
smallest threshold whose bound is within the slider's α" — reads
`conformal_bound`, which the server already rounded UP.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class AutonomyEntry(BaseModel):
    decision_type: str
    display_name: str
    automated: bool
    label_count: int
    model_id: Optional[uuid.UUID] = None
    method: Optional[str] = None
    status: Optional[str] = None
    target_error_rate: Decimal
    audit_sample_rate: Decimal
    threshold: Optional[Decimal] = None
    conformal_bound: Optional[Decimal] = None
    clopper_pearson_upper: Optional[Decimal] = None
    auto_share: Optional[Decimal] = None
    ece_before: Optional[Decimal] = None
    ece_after: Optional[Decimal] = None
    brier_after: Optional[Decimal] = None
    achievable: bool = False
    stale: bool = False
    fitted_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    suspended_at: Optional[datetime] = None
    suspended_reason: Optional[str] = None
    last_rejection: Optional[str] = None
    summary: str


class AutonomyOverview(BaseModel):
    organization_id: uuid.UUID
    as_of: datetime
    min_labels: int
    min_labels_isotonic: int
    confidence: float
    entries: list[AutonomyEntry]


class ReliabilityBin(BaseModel):
    lower: float
    upper: float
    count: int
    mean_predicted: Optional[float] = None
    mean_raw: Optional[float] = None
    observed: Optional[float] = None


class FittedCurvePoint(BaseModel):
    score: float
    probability: float


class CoveragePoint(BaseModel):
    threshold: float
    auto_share: float
    conformal_bound: float
    clopper_pearson_upper: float
    observed_error: float
    passed: int


class AutonomyReliability(BaseModel):
    entry: AutonomyEntry
    holdout_count: int
    audit_labels: int
    reliability: list[ReliabilityBin]
    reliability_raw: list[ReliabilityBin]
    fitted_curve: list[FittedCurvePoint]
    coverage_curve: list[CoveragePoint]
    last_check: Optional[dict[str, Any]] = None
    psi_threshold: float
    confidence: float
    min_labels: int
    min_labels_isotonic: int
    target_error_rate_max: Decimal
    audit_sample_rate_min: Decimal
    audit_sample_rate_max: Decimal


class AutonomySettingsRequest(BaseModel):
    """α and the audit share. Decimal strings; the server validates bounds."""

    target_error_rate: Decimal = Field(
        description="Wrong automatic approvals allowed per document, in (0, 0.2].",
    )
    audit_sample_rate: Decimal = Field(
        description="Share of automatic approvals still sent to review, in [0.01, 0.25].",
    )

    @field_validator("target_error_rate", "audit_sample_rate")
    @classmethod
    def _finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("must be a finite decimal")
        return value


class AutonomyActionResult(BaseModel):
    ok: bool
    outcome: str
    message: str
    entry: AutonomyEntry


__all__ = [
    "AutonomyActionResult",
    "AutonomyEntry",
    "AutonomyOverview",
    "AutonomyReliability",
    "AutonomySettingsRequest",
    "CoveragePoint",
    "FittedCurvePoint",
    "ReliabilityBin",
]
