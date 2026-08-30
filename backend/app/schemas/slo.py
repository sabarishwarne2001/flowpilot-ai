"""ARCH-17 — SLO API contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.slo import SLOMethod, SLOUnit, SLOWindow


class SLOTarget(BaseModel):
    slo_key: str
    display_name: str
    description: str
    unit: SLOUnit
    target_value: Decimal
    window_period: SLOWindow
    is_contractual: bool
    source: str
    definition_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None

    model_config = ConfigDict(protected_namespaces=())


class SLOComplianceEntry(BaseModel):
    slo_key: str
    target: SLOTarget

    observed_value: Optional[Decimal] = Field(
        default=None,
        description="None means no samples in the current window, not zero.",
    )
    sample_count: int
    error_count: int
    breached: bool
    method: Optional[SLOMethod] = Field(
        default=None,
        description=(
            "HISTOGRAM_INTERPOLATED means observed_value is an estimate whose "
            "error is bounded by one bucket width. The breach verdict is still "
            "exact when the target sits on a bucket boundary, which it always "
            "does for a contractual SLO."
        ),
    )

    window_start: datetime
    window_end: datetime

    breached_windows: int
    total_windows: int
    compliance_ratio: Optional[Decimal] = Field(
        default=None,
        description="Sealed windows met / sealed windows observed. None when none are sealed yet.",
    )

    model_config = ConfigDict(protected_namespaces=())


class SLOSummaryResponse(BaseModel):
    organization_id: uuid.UUID
    as_of: datetime
    period: SLOWindow
    contractual_breaches: int
    entries: list[SLOComplianceEntry]

    model_config = ConfigDict(protected_namespaces=())


class SLOTargetUpdate(BaseModel):
    target_value: Decimal = Field(ge=0)
    window_period: Optional[SLOWindow] = None
    is_contractual: bool = False
    notes: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def sane_bounds(self):
        if self.target_value < 0:
            raise ValueError("A target cannot be negative.")
        return self

    model_config = ConfigDict(protected_namespaces=())


__all__ = [
    "SLOComplianceEntry",
    "SLOSummaryResponse",
    "SLOTarget",
    "SLOTargetUpdate",
]