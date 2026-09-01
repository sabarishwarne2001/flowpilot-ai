"""ARCH-21 §4.4 — developer portal contracts.

Every latency field here is Optional and every rate field is Optional, and
that is the single most important thing about this module. `None` means "no
measurement", and the frontend renders it as such. A schema that declared
`p95_latency_ms: float = 0.0` would turn a day with no traffic into a service
that responds in zero milliseconds — the same laundering of an unknown into a
flattering number that ARCH-18 banned when it prohibited
`COALESCE(cost_basis_micros, 0)`.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.core.api_tiers import ApiRateTier
from app.core.scopes import ApiKeyScope


class ApiTierDescriptor(BaseModel):
    key: str
    display_name: str
    rank: int
    rate_limit_per_minute: int
    monthly_request_quota: int
    ef_search: int
    description: str
    assignable: bool = Field(
        description=(
            "False when this tier is above the organization's plan ceiling. "
            "Listed anyway so an admin can see what upgrading would buy."
        )
    )


class TierCataloguePayload(BaseModel):
    ceiling: str
    quota_tier_key: Optional[str] = Field(
        default=None,
        description=(
            "The organization's commercial quota tier. None means no "
            "quota_tiers version is in force, which resolves the ceiling to "
            "FREE rather than to unlimited."
        ),
    )
    tiers: list[ApiTierDescriptor]


class DeveloperKeySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    display_prefix: str = Field(
        description=(
            "Computed from the key id, never stored. There is no prefix "
            "column on api_keys; this is the id in base32 behind the "
            "environment tag, and it carries no secret."
        )
    )
    tier_key: str
    rate_limit_per_minute: int
    monthly_request_quota: int
    is_public_api_enabled: bool
    scopes: list[str]
    public_scopes: list[str]
    expires_at: Optional[str] = None
    last_used_at: Optional[str] = None
    month_to_date_requests: int
    window_requests: int
    quota_used_fraction: Optional[float] = Field(
        default=None,
        description="None when the quota is zero; a fraction is undefined, not 0.",
    )
    created_at: str


class DeveloperOverviewResponse(BaseModel):
    organization_id: str
    window_days: int
    window_start: str
    window_end: str
    tier_catalogue: TierCataloguePayload
    keys: list[DeveloperKeySummary]
    public_key_count: int
    total_key_count: int
    month_to_date_requests: int


class DeveloperUsagePoint(BaseModel):
    date: str
    request_count: int
    error_count: int
    throttled_count: int
    mean_latency_ms: Optional[float] = None


class DeveloperKeyMetricsResponse(BaseModel):
    api_key_id: str
    window_days: int
    window_start: str
    window_end: str
    total_requests: int
    total_errors: int
    total_throttled: int
    served_requests: int
    error_rate: Optional[float] = None
    mean_latency_ms: Optional[float] = None
    p50_latency_ms: Optional[float] = None
    p95_latency_ms: Optional[float] = None
    latency_method: str = Field(
        description=(
            "HISTOGRAM_INTERPOLATED. Percentiles are interpolated from a "
            "bucketed distribution and are accurate to one bucket width. "
            "They are estimates and are labelled as such."
        )
    )
    month_to_date_requests: int
    series: list[DeveloperUsagePoint]


class DeveloperTierUpdateRequest(BaseModel):
    tier_key: ApiRateTier
    enable_public_api: Optional[bool] = Field(
        default=None,
        description=(
            "Omit to leave the gateway opt-in unchanged. Setting true "
            "requires the key to already hold at least one public_* scope."
        ),
    )


class DeveloperKeyCreateRequest(BaseModel):
    """Issue a key and stamp it in one call.

    Separate from `ApiKeyCreate` because the console's issuance flow has no
    concept of a tier and must not acquire one: a key minted for an internal
    integration should stay gateway-disabled by default, which is exactly
    what happens when `is_public_api_enabled` defaults to false.
    """

    name: str = Field(min_length=1, max_length=120)
    scopes: list[ApiKeyScope] = Field(min_length=1)
    tier_key: ApiRateTier = ApiRateTier.FREE
    expires_at: Optional[datetime] = None
    enable_public_api: bool = False


class DeveloperKeyIssuedResponse(BaseModel):
    api_key: DeveloperKeySummary
    token: str = Field(
        description="Full token. Shown ONCE, never stored, never logged."
    )


class CodeSnippetSet(BaseModel):
    curl: str
    python: str
    typescript: str


class ApiExplorerOperation(BaseModel):
    operation_id: str
    method: str
    path: str
    summary: str
    required_scope: str
    snippets: CodeSnippetSet


class ApiExplorerResponse(BaseModel):
    base_url: str
    operations: list[ApiExplorerOperation]


__all__ = [
    "ApiExplorerOperation",
    "ApiExplorerResponse",
    "ApiTierDescriptor",
    "CodeSnippetSet",
    "DeveloperKeyCreateRequest",
    "DeveloperKeyIssuedResponse",
    "DeveloperKeyMetricsResponse",
    "DeveloperKeySummary",
    "DeveloperOverviewResponse",
    "DeveloperTierUpdateRequest",
    "DeveloperUsagePoint",
    "TierCataloguePayload",
]