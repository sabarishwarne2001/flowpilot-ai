"""ARCH-14 Step 7 — the tenant usage API's response shapes."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_serializer


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


__all__ = [
    "MAX_SERIES_BUCKETS",
    "UsageBucket",
    "UsageGranularity",
    "UsageLimit",
    "UsageLimitsResponse",
    "UsageLine",
    "UsagePeriod",
    "UsageSeriesResponse",
    "UsageSummaryResponse",
]