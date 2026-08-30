"""ARCH-14 Step 7 — the tenant usage metrics API."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import OrganizationContext, RequireOrgAdmin, RequireWorkspaceViewer, get_db
from app.core.principal import Principal, get_current_principal
from app.schemas.usage import (
    SpendLimitResponse,
    SpendLimitUpdate,
    UsageGranularity,
    UsageLimit,
    UsageLimitsResponse,
    UsagePeriod,
    UsageSeriesResponse,
    UsageSummaryResponse,
)
from app.services import quota_service, spend_control_service, usage_metrics_service
from app.services.usage_metrics_service import UsageQueryError

logger = logging.getLogger("app.api.v1.usage")

router = APIRouter(tags=["Usage"])
workspace_router = APIRouter(tags=["Usage"])

_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

MAX_SERIES_LOOKBACK = timedelta(days=800)


def _parse_at(raw: Optional[str]) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)

    month = _MONTH_RE.match(raw)
    if month:
        return datetime(
            int(month.group(1)), int(month.group(2)), 1, tzinfo=timezone.utc
        )
    day = _DAY_RE.match(raw)
    if day:
        return datetime(
            int(day.group(1)),
            int(day.group(2)),
            int(day.group(3)),
            tzinfo=timezone.utc,
        )
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`at` must be YYYY-MM, YYYY-MM-DD, or an ISO-8601 instant.",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _require(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ============================================================================
# Organization scope
# ============================================================================


@router.get(
    "/organizations/{organization_id}/usage/summary",
    response_model=UsageSummaryResponse,
    summary="Usage summary for a period",
)
def get_usage_summary(
    organization_id: uuid.UUID,
    period: UsagePeriod = Query(UsagePeriod.MONTH),
    at: Optional[str] = Query(
        None,
        description="YYYY-MM, YYYY-MM-DD, or an ISO-8601 instant. Defaults to now.",
    ),
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> UsageSummaryResponse:
    return usage_metrics_service.summary(
        db,
        organization_id=context.organization_id,
        period=period,
        at=_parse_at(at),
    )


@router.get(
    "/organizations/{organization_id}/usage/series",
    response_model=UsageSeriesResponse,
    summary="Usage over time",
)
def get_usage_series(
    organization_id: uuid.UUID,
    granularity: UsageGranularity = Query(UsageGranularity.DAY),
    range_from: datetime = Query(..., alias="from"),
    range_to: Optional[datetime] = Query(None, alias="to"),
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> UsageSeriesResponse:
    since = _require(range_from, name="from")
    until = _require(range_to, name="to") if range_to else None

    if datetime.now(timezone.utc) - since > MAX_SERIES_LOOKBACK:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"`from` is more than {MAX_SERIES_LOOKBACK.days} days ago. "
                "Use a coarser granularity or a narrower range."
            ),
        )

    try:
        return usage_metrics_service.series(
            db,
            organization_id=context.organization_id,
            granularity=granularity,
            since=since,
            until=until,
        )
    except UsageQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get(
    "/organizations/{organization_id}/usage/limits",
    response_model=UsageLimitsResponse,
    summary="Effective quotas and how much is used",
)
def get_usage_limits(
    organization_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> UsageLimitsResponse:
    moment = datetime.now(timezone.utc)
    tier = quota_service.resolve_tier(
        db, organization_id=context.organization_id, at=moment
    )
    statuses = quota_service.quota_status(
        db, organization_id=context.organization_id, at=moment
    )

    return UsageLimitsResponse(
        organization_id=context.organization_id,
        quota_tier_key=tier.key if tier else None,
        quota_tier_version=tier.version if tier else None,
        quota_tier_display_name=tier.display_name if tier else None,
        as_of=moment,
        limits=[
            UsageLimit(
                limit_key=item.limit_key,
                period=item.period,
                source=item.source,
                max_quantity=item.max_quantity,
                max_cost_micros=item.max_cost_micros,
                current_quantity=item.current_quantity,
                current_cost_micros=item.current_cost_micros,
                remaining_quantity=item.remaining_quantity,
                remaining_cost_micros=item.remaining_cost_micros,
                overage_policy=item.overage_policy,
                grace_quantity=item.grace_quantity,
                hard_stop=item.hard_stop,
                quota_tier_key=item.quota_tier_key,
                quota_tier_version=item.quota_tier_version,
                period_start=item.period_start,
                resets_at=item.resets_at,
            )
            for item in statuses
        ],
    )


@router.get(
    "/organizations/{organization_id}/usage-limits",
    response_model=list[SpendLimitResponse],
    summary="List custom organization spend limits",
)
def list_usage_limits(
    organization_id: uuid.UUID,
    include_inactive: bool = Query(
        False,
        description="Include superseded rows.",
    ),
    context: OrganizationContext = Depends(RequireOrgAdmin),
    db: Session = Depends(get_db),
) -> list[SpendLimitResponse]:
    if context.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )

    limits = spend_control_service.list_limits(
        db,
        organization_id=organization_id,
        include_inactive=include_inactive,
    )
    return [SpendLimitResponse.model_validate(limit) for limit in limits]


@router.put(
    "/organizations/{organization_id}/usage-limits",
    response_model=SpendLimitResponse,
    summary="Create or update custom organization spend limits",
)
def update_usage_limit(
    organization_id: uuid.UUID,
    payload: SpendLimitUpdate,
    context: OrganizationContext = Depends(RequireOrgAdmin),
    db: Session = Depends(get_db),
) -> SpendLimitResponse:
    if context.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )

    principal = get_current_principal() or Principal.for_user(context.user.id)

    limit = spend_control_service.set_limit(
        db,
        organization_id=organization_id,
        limit_key=payload.limit_key,
        period=payload.period,
        max_quantity=payload.max_quantity,
        max_cost_micros=payload.max_cost_micros,
        hard_stop=payload.hard_stop,
        note=payload.note,
        principal=principal,
    )
    db.commit()
    db.refresh(limit)
    return SpendLimitResponse.model_validate(limit)


# ============================================================================
# Workspace scope
# ============================================================================


@workspace_router.get(
    "/summary",
    response_model=UsageSummaryResponse,
    summary="Usage summary for one workspace",
)
def get_workspace_usage_summary(
    period: UsagePeriod = Query(UsagePeriod.MONTH),
    at: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    context=Depends(RequireWorkspaceViewer),
) -> UsageSummaryResponse:
    return usage_metrics_service.summary(
        db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        period=period,
        at=_parse_at(at),
    )


@workspace_router.get(
    "/series",
    response_model=UsageSeriesResponse,
    summary="Usage over time for one workspace",
)
def get_workspace_usage_series(
    granularity: UsageGranularity = Query(UsageGranularity.DAY),
    range_from: datetime = Query(..., alias="from"),
    range_to: Optional[datetime] = Query(None, alias="to"),
    db: Session = Depends(get_db),
    context=Depends(RequireWorkspaceViewer),
) -> UsageSeriesResponse:
    try:
        return usage_metrics_service.series(
            db,
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            granularity=granularity,
            since=_require(range_from, name="from"),
            until=_require(range_to, name="to") if range_to else None,
        )
    except UsageQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


__all__ = ["router", "workspace_router"]