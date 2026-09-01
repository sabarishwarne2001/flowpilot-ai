"""ARCH-21 §3.2 — the public API rate tier vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ApiRateTier(str, Enum):
    FREE = "FREE"
    BUILDER = "BUILDER"
    PRO = "PRO"
    ENTERPRISE = "ENTERPRISE"


API_RATE_TIER_VALUES: tuple[str, ...] = tuple(t.value for t in ApiRateTier)
DEFAULT_API_RATE_TIER: ApiRateTier = ApiRateTier.FREE

EF_SEARCH_FLOOR: int = 40
EF_SEARCH_CEILING: int = 200


@dataclass(frozen=True)
class ApiTierProfile:
    key: ApiRateTier
    display_name: str
    rank: int
    rate_limit_per_minute: int
    monthly_request_quota: int
    ef_search: int
    description: str

    @property
    def value(self) -> str:
        return self.key.value


TIER_PROFILES: dict[ApiRateTier, ApiTierProfile] = {
    ApiRateTier.FREE: ApiTierProfile(
        key=ApiRateTier.FREE,
        display_name="Free",
        rank=0,
        rate_limit_per_minute=60,
        monthly_request_quota=10_000,
        ef_search=40,
        description="Evaluation and prototyping. Platform-default vector recall.",
    ),
    ApiRateTier.BUILDER: ApiTierProfile(
        key=ApiRateTier.BUILDER,
        display_name="Builder",
        rank=1,
        rate_limit_per_minute=300,
        monthly_request_quota=250_000,
        ef_search=60,
        description="Single-application integrations with steady, bounded traffic.",
    ),
    ApiRateTier.PRO: ApiTierProfile(
        key=ApiRateTier.PRO,
        display_name="Pro",
        rank=2,
        rate_limit_per_minute=1_200,
        monthly_request_quota=2_500_000,
        ef_search=100,
        description="Production workloads. Raised candidate depth for higher recall.",
    ),
    ApiRateTier.ENTERPRISE: ApiTierProfile(
        key=ApiRateTier.ENTERPRISE,
        display_name="Enterprise",
        rank=3,
        rate_limit_per_minute=6_000,
        monthly_request_quota=50_000_000,
        ef_search=160,
        description="High-concurrency programs. Maximum candidate depth.",
    ),
}

QUOTA_TIER_CEILING: dict[str, ApiRateTier] = {
    "free": ApiRateTier.FREE,
    "developer": ApiRateTier.BUILDER,
    "business": ApiRateTier.PRO,
    "enterprise": ApiRateTier.ENTERPRISE,
}


class ApiTierError(ValueError):
    pass


def parse_tier(value: object) -> ApiRateTier:
    if isinstance(value, ApiRateTier):
        return value
    try:
        return ApiRateTier(str(value).strip().upper())
    except ValueError as exc:
        raise ApiTierError(
            f"{value!r} is not a known API rate tier. Known tiers: "
            f"{', '.join(API_RATE_TIER_VALUES)}."
        ) from exc


def profile_for(tier: object) -> ApiTierProfile:
    return TIER_PROFILES[parse_tier(tier)]


def ceiling_for_quota_tier(quota_tier_key: Optional[str]) -> ApiRateTier:
    if not quota_tier_key:
        return DEFAULT_API_RATE_TIER
    return QUOTA_TIER_CEILING.get(
        str(quota_tier_key).strip().lower(), DEFAULT_API_RATE_TIER
    )


def is_within_ceiling(tier: object, ceiling: object) -> bool:
    return profile_for(tier).rank <= profile_for(ceiling).rank


def assignable_tiers(ceiling: object) -> list[ApiTierProfile]:
    limit = profile_for(ceiling).rank
    return sorted(
        (p for p in TIER_PROFILES.values() if p.rank <= limit),
        key=lambda p: p.rank,
    )


def clamp_ef_search(value: int) -> int:
    return max(EF_SEARCH_FLOOR, min(EF_SEARCH_CEILING, int(value)))


def ef_search_for(tier: object) -> int:
    return clamp_ef_search(profile_for(tier).ef_search)


def rate_limit_for(tier: object) -> int:
    return profile_for(tier).rate_limit_per_minute


def monthly_quota_for(tier: object) -> int:
    return profile_for(tier).monthly_request_quota


__all__ = [
    "API_RATE_TIER_VALUES",
    "ApiRateTier",
    "ApiTierError",
    "ApiTierProfile",
    "DEFAULT_API_RATE_TIER",
    "EF_SEARCH_CEILING",
    "EF_SEARCH_FLOOR",
    "QUOTA_TIER_CEILING",
    "TIER_PROFILES",
    "assignable_tiers",
    "ceiling_for_quota_tier",
    "clamp_ef_search",
    "ef_search_for",
    "is_within_ceiling",
    "monthly_quota_for",
    "parse_tier",
    "profile_for",
    "rate_limit_for",
]