"""ARCH-14 Step 7 — the tenant usage API's response shapes."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.models.spend_limit import SpendLimitPeriod


class UsagePeriod(str, Enum):
    DAY = "DAY"
    MONTH = "MONTH"


class UsageGranularity(str, Enum):
    HOUR = "HOUR"
    DAY = "DAY"
    MONTH = "MONTH"


MAX_SERIES_BUCKETS: int = 800


class UsageLine(BaseModel):
    """One event type's contribution to a period."""

    event_type: str
    unit: str
    quantity: Decimal
    estimated_quantity: Decimal
    cost_micros: int
    estimated_cost_micros: int
    event_count: int
    late_quantity: Decimal
    late_cost_micros: int

    model_config = ConfigDict(protected_namespaces=())

    @field_serializer("quantity", "estimated_quantity", "late_quantity")
    def _decimal_as_string(self, value: Decimal) -> str:
        return format(value, "f")


class UsageSummaryResponse(BaseModel):
    organization_id: uuid.UUID
    workspace_id: Optional[uuid.UUID] = None
    period: UsagePeriod
    period_start: datetime
    period_end: datetime
    currency: str = "USD"
    sealed: bool
    sealed_at: Optional[datetime] = None
    as_of: Optional[datetime] = None

    lines: list[UsageLine] = []
    total_cost_micros: int = 0
    estimated_cost_micros: int = 0
    late_cost_micros: int = 0

    model_config = ConfigDict(protected_namespaces=())


class UsageBucket(BaseModel):
    bucket_start: datetime
    bucket_end: datetime
    sealed: bool
    lines: list[UsageLine] = []
    total_cost_micros: int = 0
    estimated_cost_micros: int = 0

    # ---- ARCH-24 ---------------------------------------------------------
    #
    # Optional and defaulting to None, not 0. This DTO is serialised on
    # customer-facing usage paths as well as internal ones, so the field being
    # absent must be indistinguishable from the cost being unknown \u2014 and both
    # must be distinguishable from the cost being nothing.
    #
    # Note this is supplier cost, not customer price. It is only populated on
    # superadmin-gated reads; the tenant-facing serialiser leaves it None.
    cost_basis_micros: Optional[int] = None
    unknown_cost_basis_event_count: int = 0
    cost_basis_is_complete: Optional[bool] = None

    model_config = ConfigDict(protected_namespaces=())


class UsageSeriesResponse(BaseModel):
    organization_id: uuid.UUID
    workspace_id: Optional[uuid.UUID] = None
    granularity: UsageGranularity
    range_start: datetime
    range_end: datetime
    currency: str = "USD"
    buckets: list[UsageBucket] = []
    total_cost_micros: int = 0
    estimated_cost_micros: int = 0

    model_config = ConfigDict(protected_namespaces=())


class UsageLimit(BaseModel):
    limit_key: str
    period: str
    source: str
    max_quantity: Optional[Decimal] = None
    max_cost_micros: Optional[int] = None
    current_quantity: Decimal
    current_cost_micros: int
    remaining_quantity: Optional[Decimal] = None
    remaining_cost_micros: Optional[int] = None
    overage_policy: str
    grace_quantity: Optional[Decimal] = None
    hard_stop: bool
    quota_tier_key: Optional[str] = None
    quota_tier_version: Optional[int] = None
    period_start: datetime
    resets_at: datetime

    model_config = ConfigDict(protected_namespaces=())

    @field_serializer(
        "max_quantity",
        "current_quantity",
        "remaining_quantity",
        "grace_quantity",
    )
    def _decimal_as_string(self, value: Optional[Decimal]) -> Optional[str]:
        return None if value is None else format(value, "f")


class UsageLimitsResponse(BaseModel):
    organization_id: uuid.UUID
    quota_tier_key: Optional[str] = None
    quota_tier_version: Optional[int] = None
    quota_tier_display_name: Optional[str] = None
    as_of: datetime
    limits: list[UsageLimit] = []

    model_config = ConfigDict(protected_namespaces=())


class SpendLimitUpdate(BaseModel):
    limit_key: str = Field(min_length=1, max_length=100)
    period: SpendLimitPeriod
    max_quantity: Decimal | None = None
    max_cost_micros: int | None = Field(default=None, ge=0)
    hard_stop: bool = True
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_ceiling(self):
        if self.max_quantity is None and self.max_cost_micros is None:
            raise ValueError(
                "At least one of max_quantity or max_cost_micros is required."
            )
        return self


class SpendLimitResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    limit_key: str
    period: SpendLimitPeriod
    max_quantity: Decimal | None
    max_cost_micros: int | None
    hard_stop: bool
    is_active: bool
    note: str | None

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())


# =============================================================================
# Plan list endpoint schemas (Deliverable A)
# =============================================================================


class PlanEntitlement(BaseModel):
    """One metered limit inside a plan, as the server computed it."""

    event_type: str
    limit_quantity: Optional[int] = None
    limit_cost_micros: Optional[int] = None
    overage_policy: str
    period: str

    model_config = ConfigDict(protected_namespaces=())


class PlanOption(BaseModel):
    """A tier a customer could subscribe to."""

    key: str
    display_name: str
    version: int
    is_current: bool
    price_id: Optional[str] = None
    unit_amount: Optional[int] = None
    currency: Optional[str] = None
    interval: Optional[str] = None
    entitlements: list[PlanEntitlement] = Field(default_factory=list)
    notes: Optional[str] = None

    model_config = ConfigDict(protected_namespaces=())


class PlanListResponse(BaseModel):
    organization_id: uuid.UUID
    current_tier_key: Optional[str] = None
    as_of: datetime
    plans: list[PlanOption]

    model_config = ConfigDict(protected_namespaces=())


class UsageCostBasisSummary(BaseModel):
    """Rollup cost basis over a window, with its incompleteness stated.

    `known_share` exists so a reader cannot accidentally treat a 12%-priced
    window as a margin figure. It mirrors `margin_service.is_trustworthy`
    at the rollup grain: the threshold lives on the backend and travels to the
    client, which never recomputes it.
    """

    organization_id: uuid.UUID
    range_start: datetime
    range_end: datetime
    granularity: UsageGranularity

    event_count: int = 0
    cost_micros: int = 0

    cost_basis_micros: Optional[int] = None
    unknown_cost_basis_event_count: int = 0
    cost_basis_source_mix: dict[str, int] = {}

    is_trustworthy: bool = False
    known_share: Optional[float] = None

    model_config = ConfigDict(protected_namespaces=())


__all__ = [
    "MAX_SERIES_BUCKETS",
    "PlanEntitlement",
    "PlanListResponse",
    "PlanOption",
    "SpendLimitPeriod",
    "SpendLimitResponse",
    "SpendLimitUpdate",
    "UsageBucket",
    "UsageCostBasisSummary",
    "UsageGranularity",
    "UsageLimit",
    "UsageLimitsResponse",
    "UsageLine",
    "UsagePeriod",
    "UsageSeriesResponse",
    "UsageSummaryResponse",
]