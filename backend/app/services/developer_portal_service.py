"""ARCH-21 §3.4 — what the developer portal reads and writes.

TIER ASSIGNMENT IS CEILINGED, NOT FREE-FORM (decision D3)
=========================================================

`assign_tier()` refuses any tier above `ceiling_for_quota_tier(<the org's
quota tier>)`. Without that refusal an organization on the `business` quota
tier could stamp its own key ENTERPRISE through the portal and receive five
times the throughput it is billed for. The check is here rather than in the
router because the router is not the only caller — the tests are, and so is
any future CLI.

`quota_service.resolve_tier()` returning None (no `quota_tiers` row in force
for this organization) resolves to a FREE ceiling. That direction is chosen
deliberately: the accounts with no tier in force are the newest and least
scrutinised ones, and defaulting them to ENTERPRISE would hand unlimited
throughput to exactly the population an abuse review has not reached yet.

PERCENTILES ARE INTERPOLATED AND SAY SO
=======================================

`api_key_usage_daily` carries a bucketed latency histogram, so p50/p95 come
from `slo_service.Histogram.percentile()` — the same estimator ARCH-17 uses,
accurate to one bucket width, and labelled `HISTOGRAM_INTERPOLATED` in the
response so nobody reads an estimate as a measurement.

What this module will NOT do is derive a percentile from
`total_latency_ms / request_count`. That number is a mean; presenting it as a
p95 would be a fabricated figure of exactly the kind ARCH-18 forbade when it
banned `COALESCE(cost_basis_micros, 0)`. When a day has no served requests,
every latency figure for it is None and renders as "no data", never as zero.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.api_tiers import (
    TIER_PROFILES,
    ApiRateTier,
    ApiTierProfile,
    assignable_tiers,
    ceiling_for_quota_tier,
    is_within_ceiling,
    monthly_quota_for,
    parse_tier,
    profile_for,
    rate_limit_for,
)
from app.core.exceptions import OrganizationPermissionDeniedError
from app.core.scopes import PUBLIC_API_SCOPES, ApiKeyScope
from app.models.api_key import ApiKey
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.organization import OrganizationMember, OrganizationRole
from app.models.public_api import (
    ApiKeyUsageDaily,
    LATENCY_BOUNDS_MS,
    LATENCY_BUCKET_COUNT,
)
from app.services import audit_service, public_api_service

logger = logging.getLogger("app.services.developer_portal")

#: Reported alongside every percentile so the estimate is never mistaken for
#: a measured value. Matches app/models/slo.py::SLOMethod.
LATENCY_METHOD_INTERPOLATED: str = "HISTOGRAM_INTERPOLATED"

DEFAULT_WINDOW_DAYS: int = 30
MAX_WINDOW_DAYS: int = 90


class DeveloperPortalError(Exception):
    """Base class for portal refusals."""


class TierCeilingExceededError(DeveloperPortalError):
    """The requested tier is above what the organization's plan allows."""


# ===========================================================================
# Tier resolution
# ===========================================================================


def organization_quota_tier_key(
    db: Session, *, organization_id: uuid.UUID
) -> Optional[str]:
    """The organization's commercial quota tier key, or None.

    Wrapped rather than called directly so the None case has one documented
    meaning: no `quota_tiers` version is in force for this organization right
    now. `quota_service` already logs the reason at WARNING; repeating the
    lookup logic here would be a second place for it to drift.
    """
    from app.services import quota_service

    snapshot = quota_service.resolve_tier(db, organization_id=organization_id)
    return getattr(snapshot, "key", None) if snapshot is not None else None


def tier_ceiling(db: Session, *, organization_id: uuid.UUID) -> ApiRateTier:
    return ceiling_for_quota_tier(
        organization_quota_tier_key(db, organization_id=organization_id)
    )


@dataclass(frozen=True)
class TierCatalogue:
    ceiling: ApiRateTier
    quota_tier_key: Optional[str]
    profiles: list[ApiTierProfile]

    def as_payload(self) -> dict[str, Any]:
        return {
            "ceiling": self.ceiling.value,
            "quota_tier_key": self.quota_tier_key,
            "tiers": [
                {
                    "key": p.value,
                    "display_name": p.display_name,
                    "rank": p.rank,
                    "rate_limit_per_minute": p.rate_limit_per_minute,
                    "monthly_request_quota": p.monthly_request_quota,
                    "ef_search": p.ef_search,
                    "description": p.description,
                    "assignable": p.rank <= profile_for(self.ceiling).rank,
                }
                # Every tier is listed, including the ones above the
                # ceiling. Hiding them would leave an admin unable to see
                # that a higher tier exists or what upgrading would buy;
                # `assignable` carries the refusal instead of the omission.
                for p in sorted(TIER_PROFILES.values(), key=lambda x: x.rank)
            ],
        }


def tier_catalogue(db: Session, *, organization_id: uuid.UUID) -> TierCatalogue:
    quota_key = organization_quota_tier_key(db, organization_id=organization_id)
    ceiling = ceiling_for_quota_tier(quota_key)
    return TierCatalogue(
        ceiling=ceiling,
        quota_tier_key=quota_key,
        profiles=assignable_tiers(ceiling),
    )


# ===========================================================================
# Writes
# ===========================================================================


def assign_tier(
    db: Session,
    *,
    key: ApiKey,
    tier: ApiRateTier | str,
    actor: OrganizationMember,
    enable_public_api: Optional[bool] = None,
) -> ApiKey:
    """Stamp a rate tier onto a key, refusing anything above the ceiling."""
    if actor.role not in (OrganizationRole.OWNER, OrganizationRole.ADMIN):
        raise OrganizationPermissionDeniedError(
            "Only organization admins can change API key tiers."
        )

    requested = parse_tier(tier)
    ceiling = tier_ceiling(db, organization_id=key.organization_id)

    if not is_within_ceiling(requested, ceiling):
        raise TierCeilingExceededError(
            f"Tier {requested.value} exceeds this organization's plan "
            f"ceiling of {ceiling.value}. Upgrade the subscription to raise "
            "the ceiling."
        )

    target_enabled = (
        key.is_public_api_enabled
        if enable_public_api is None
        else bool(enable_public_api)
    )

    if target_enabled and not (set(key.scopes) & {s.value for s in PUBLIC_API_SCOPES}):
        # Refused here as well as by ck_api_keys_public_enabled_requires_scope.
        # The database constraint is the guarantee; this is the readable
        # error, because an IntegrityError surfacing to an admin as a 500 is
        # not an explanation.
        raise DeveloperPortalError(
            "A key cannot be enabled for the public API without at least one "
            "public_* scope. Grant a gateway scope first."
        )

    previous = {
        "tier_key": key.tier_key,
        "rate_limit_per_minute": key.rate_limit_per_minute,
        "monthly_request_quota": key.monthly_request_quota,
        "is_public_api_enabled": key.is_public_api_enabled,
    }

    key.tier_key = requested.value
    key.rate_limit_per_minute = rate_limit_for(requested)
    key.monthly_request_quota = monthly_quota_for(requested)
    key.is_public_api_enabled = target_enabled
    db.add(key)

    audit_service.record(
        db,
        organization_id=key.organization_id,
        actor_id=actor.user_id,
        resource_type=AuditResourceType.API_KEY,
        resource_id=key.id,
        action=AuditAction.UPDATED,
        details={
            "key_name": key.name,
            "change": "api_rate_tier",
            "from": previous,
            "to": {
                "tier_key": key.tier_key,
                "rate_limit_per_minute": key.rate_limit_per_minute,
                "monthly_request_quota": key.monthly_request_quota,
                "is_public_api_enabled": key.is_public_api_enabled,
            },
            "plan_ceiling": ceiling.value,
        },
    )
    return key


# ===========================================================================
# Reads
# ===========================================================================


def _window(days: Optional[int]) -> tuple[date, date, int]:
    span = max(1, min(MAX_WINDOW_DAYS, int(days or DEFAULT_WINDOW_DAYS)))
    end = datetime.now(UTC).date()
    return end - timedelta(days=span - 1), end, span


def _histogram(counts: list[int], sample_count: int, error_count: int):
    from app.services.slo_service import Histogram

    normalised = list(counts or [])
    if len(normalised) < LATENCY_BUCKET_COUNT:
        normalised += [0] * (LATENCY_BUCKET_COUNT - len(normalised))

    return Histogram(
        bounds=LATENCY_BOUNDS_MS,
        counts=tuple(int(c or 0) for c in normalised[:LATENCY_BUCKET_COUNT]),
        sample_count=int(sample_count),
        error_count=int(error_count),
        sum_value=Decimal("0"),
    )


def _percentiles(
    counts: list[int], served: int, errors: int
) -> tuple[Optional[float], Optional[float]]:
    """p50 and p95, or (None, None) when nothing was served.

    None rather than 0.0. A window with no served traffic has no latency
    distribution, and rendering that as zero milliseconds tells an operator
    the service is instantaneous at precisely the moment it served nothing.
    """
    if served <= 0:
        return None, None
    histogram = _histogram(counts, served, errors)
    return (
        float(histogram.percentile(0.50)),
        float(histogram.percentile(0.95)),
    )


@dataclass(frozen=True)
class DailyPoint:
    day: date
    request_count: int
    error_count: int
    throttled_count: int
    mean_latency_ms: Optional[float]

    def as_payload(self) -> dict[str, Any]:
        return {
            "date": self.day.isoformat(),
            "request_count": self.request_count,
            "error_count": self.error_count,
            "throttled_count": self.throttled_count,
            "mean_latency_ms": self.mean_latency_ms,
        }


def key_metrics(
    db: Session,
    *,
    organization_id: uuid.UUID,
    api_key_id: uuid.UUID,
    days: Optional[int] = None,
) -> dict[str, Any]:
    """Per-key consumption for the developer portal charts."""
    start, end, span = _window(days)

    rows = (
        db.execute(
            select(ApiKeyUsageDaily)
            .where(
                ApiKeyUsageDaily.api_key_id == api_key_id,
                ApiKeyUsageDaily.organization_id == organization_id,
                ApiKeyUsageDaily.usage_date >= start,
                ApiKeyUsageDaily.usage_date <= end,
            )
            .order_by(ApiKeyUsageDaily.usage_date.asc())
        )
        .scalars()
        .all()
    )

    by_day = {row.usage_date: row for row in rows}
    series: list[DailyPoint] = []
    for offset in range(span):
        day = start + timedelta(days=offset)
        row = by_day.get(day)
        if row is None:
            # A day with no row is a day with no traffic. Zero requests is a
            # measurement; None latency is the absence of one. They are not
            # the same and the chart must not conflate them.
            series.append(DailyPoint(day, 0, 0, 0, None))
        else:
            series.append(
                DailyPoint(
                    day,
                    int(row.request_count),
                    int(row.error_count),
                    int(row.throttled_count),
                    row.mean_latency_ms,
                )
            )

    total_requests = sum(p.request_count for p in series)
    total_errors = sum(p.error_count for p in series)
    total_throttled = sum(p.throttled_count for p in series)
    served = total_requests - total_throttled

    merged = [0] * LATENCY_BUCKET_COUNT
    latency_sum = 0
    for row in rows:
        buckets = list(row.latency_bucket_counts or [])
        for index in range(min(LATENCY_BUCKET_COUNT, len(buckets))):
            merged[index] += int(buckets[index] or 0)
        latency_sum += int(row.total_latency_ms or 0)

    p50, p95 = _percentiles(merged, served, total_errors)

    # ARCH-22 N2. This block previously re-implemented, inline, the query
    # already published as public_api_service.monthly_request_count. The
    # duplicate meant that function was defined in app/, exported in __all__,
    # and reachable only from tests/ — the orphaned-guard pattern this
    # codebase keeps re-shipping. Two copies of a billing-adjacent aggregate
    # is also one copy too many: they would have drifted the first time the
    # month boundary rule changed.
    month_requests = public_api_service.monthly_request_count(
        db,
        api_key_id=api_key_id,
        at=datetime.combine(end, time.min, tzinfo=UTC),
    )

    return {
        "api_key_id": str(api_key_id),
        "window_days": span,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "total_requests": total_requests,
        "total_errors": total_errors,
        "total_throttled": total_throttled,
        "served_requests": served,
        # A rate over zero requests is undefined, not zero.
        "error_rate": (
            (total_errors / total_requests) if total_requests else None
        ),
        "mean_latency_ms": (
            (latency_sum / served) if served > 0 else None
        ),
        "p50_latency_ms": p50,
        "p95_latency_ms": p95,
        "latency_method": LATENCY_METHOD_INTERPOLATED,
        "month_to_date_requests": month_requests,
        "series": [point.as_payload() for point in series],
    }


def organization_overview(
    db: Session, *, organization_id: uuid.UUID, days: Optional[int] = None
) -> dict[str, Any]:
    """Tier ceiling, gateway keys and their quota consumption."""
    start, end, span = _window(days)
    catalogue = tier_catalogue(db, organization_id=organization_id)

    keys = (
        db.execute(
            select(ApiKey)
            .where(
                ApiKey.organization_id == organization_id,
                ApiKey.deactivated_at.is_(None),
            )
            .order_by(ApiKey.created_at.desc())
        )
        .scalars()
        .all()
    )

    month_start = end.replace(day=1)
    month_rows = dict(
        db.execute(
            select(
                ApiKeyUsageDaily.api_key_id,
                func.coalesce(func.sum(ApiKeyUsageDaily.request_count), 0),
            )
            .where(
                ApiKeyUsageDaily.organization_id == organization_id,
                ApiKeyUsageDaily.usage_date >= month_start,
            )
            .group_by(ApiKeyUsageDaily.api_key_id)
        ).all()
    )

    window_rows = dict(
        db.execute(
            select(
                ApiKeyUsageDaily.api_key_id,
                func.coalesce(func.sum(ApiKeyUsageDaily.request_count), 0),
            )
            .where(
                ApiKeyUsageDaily.organization_id == organization_id,
                ApiKeyUsageDaily.usage_date >= start,
                ApiKeyUsageDaily.usage_date <= end,
            )
            .group_by(ApiKeyUsageDaily.api_key_id)
        ).all()
    )

    entries: list[dict[str, Any]] = []
    for key in keys:
        used = int(month_rows.get(key.id, 0) or 0)
        quota = int(key.monthly_request_quota or 0)
        entries.append(
            {
                "id": str(key.id),
                "name": key.name,
                "display_prefix": key.display_prefix,
                "tier_key": key.tier_key,
                "rate_limit_per_minute": key.rate_limit_per_minute,
                "monthly_request_quota": quota,
                "is_public_api_enabled": key.is_public_api_enabled,
                "scopes": list(key.scopes),
                "public_scopes": sorted(
                    s for s in key.scopes
                    if s in {scope.value for scope in PUBLIC_API_SCOPES}
                ),
                "expires_at": (
                    key.expires_at.isoformat() if key.expires_at else None
                ),
                "last_used_at": (
                    key.last_used_at.isoformat() if key.last_used_at else None
                ),
                "month_to_date_requests": used,
                "window_requests": int(window_rows.get(key.id, 0) or 0),
                "quota_used_fraction": (used / quota) if quota > 0 else None,
                "created_at": key.created_at.isoformat(),
            }
        )

    enabled = [e for e in entries if e["is_public_api_enabled"]]

    return {
        "organization_id": str(organization_id),
        "window_days": span,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "tier_catalogue": catalogue.as_payload(),
        "keys": entries,
        "public_key_count": len(enabled),
        "total_key_count": len(entries),
        "month_to_date_requests": sum(
            int(v or 0) for v in month_rows.values()
        ),
    }


# ===========================================================================
# Code snippets
# ===========================================================================

#: The token placeholder shown in every snippet. A real token is displayed
#: once at issuance and never again — the portal has no way to interpolate
#: one here even if that were desirable, and it is not.
TOKEN_PLACEHOLDER: str = "$FLOWPILOT_API_KEY"


def code_snippets(
    *, base_url: str, path: str, method: str, body: Optional[dict[str, Any]] = None
) -> dict[str, str]:
    """cURL / Python / TypeScript for one gateway call.

    Generated server-side rather than in the browser so that the request
    shape shown to a developer comes from the same place the routes do. A
    snippet built in the frontend drifts the first time a path changes.
    """
    import json

    url = f"{base_url.rstrip('/')}{path}"
    verb = method.upper()
    payload = json.dumps(body, indent=2) if body else None

    if payload:
        curl = (
            f"curl -X {verb} '{url}' \\\n"
            f"  -H 'Authorization: Bearer {TOKEN_PLACEHOLDER}' \\\n"
            f"  -H 'Content-Type: application/json' \\\n"
            f"  -d '{json.dumps(body)}'"
        )
        python = (
            "import os, requests\n\n"
            f"response = requests.{verb.lower()}(\n"
            f"    \"{url}\",\n"
            "    headers={\"Authorization\": f\"Bearer {os.environ['FLOWPILOT_API_KEY']}\"},\n"
            f"    json={json.dumps(body)},\n"
            "    timeout=30,\n"
            ")\n"
            "response.raise_for_status()\n"
            "print(response.json())\n"
            "# Rate limit budget is on the response, not in the body:\n"
            "print(response.headers['X-RateLimit-Remaining'])"
        )
        typescript = (
            f"const response = await fetch(\"{url}\", {{\n"
            f"  method: \"{verb}\",\n"
            "  headers: {\n"
            "    Authorization: `Bearer ${process.env.FLOWPILOT_API_KEY}`,\n"
            "    \"Content-Type\": \"application/json\",\n"
            "  },\n"
            f"  body: JSON.stringify({json.dumps(body)}),\n"
            "});\n\n"
            "if (!response.ok) throw new Error(await response.text());\n"
            "console.log(response.headers.get(\"X-RateLimit-Remaining\"));\n"
            "console.log(await response.json());"
        )
    else:
        curl = (
            f"curl -X {verb} '{url}' \\\n"
            f"  -H 'Authorization: Bearer {TOKEN_PLACEHOLDER}'"
        )
        python = (
            "import os, requests\n\n"
            f"response = requests.{verb.lower()}(\n"
            f"    \"{url}\",\n"
            "    headers={\"Authorization\": f\"Bearer {os.environ['FLOWPILOT_API_KEY']}\"},\n"
            "    timeout=30,\n"
            ")\n"
            "response.raise_for_status()\n"
            "print(response.json())\n"
            "print(response.headers['X-RateLimit-Remaining'])"
        )
        typescript = (
            f"const response = await fetch(\"{url}\", {{\n"
            f"  method: \"{verb}\",\n"
            "  headers: { Authorization: `Bearer ${process.env.FLOWPILOT_API_KEY}` },\n"
            "});\n\n"
            "if (!response.ok) throw new Error(await response.text());\n"
            "console.log(response.headers.get(\"X-RateLimit-Remaining\"));\n"
            "console.log(await response.json());"
        )

    return {"curl": curl, "python": python, "typescript": typescript}


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "DailyPoint",
    "DeveloperPortalError",
    "LATENCY_METHOD_INTERPOLATED",
    "MAX_WINDOW_DAYS",
    "TOKEN_PLACEHOLDER",
    "TierCatalogue",
    "TierCeilingExceededError",
    "assign_tier",
    "code_snippets",
    "key_metrics",
    "organization_overview",
    "organization_quota_tier_key",
    "tier_catalogue",
    "tier_ceiling",
]