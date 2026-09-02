"""ARCH-14 Step 7 — the read layer behind the tenant usage API."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.usage_events import USAGE_EVENT_TYPES
from app.models.usage_rollup import TOTAL_EVENT_TYPE, RollupWindow, UsageRollup
from app.schemas.usage import (
    MAX_SERIES_BUCKETS,
    UsageBucket,
    UsageGranularity,
    UsageLine,
    UsagePeriod,
    UsageSeriesResponse,
    UsageSummaryResponse,
)
from app.services import rollup_service

logger = logging.getLogger("app.services.usage_metrics")

DEFAULT_CURRENCY = "USD"


class UsageQueryError(ValueError):
    """The requested window is not one this endpoint will serve."""


_PERIOD_GRANULARITY = {
    UsagePeriod.DAY: rollup_service.DAY,
    UsagePeriod.MONTH: rollup_service.MONTH,
}


def period_bounds(period: UsagePeriod, at: datetime) -> tuple[datetime, datetime]:
    granularity = _PERIOD_GRANULARITY[period]
    start = rollup_service.bucket_start_for(granularity, at)
    return start, rollup_service.bucket_end(granularity, start)


def _unit_for(event_type: str) -> str:
    descriptor = USAGE_EVENT_TYPES.get(event_type)
    if descriptor is not None:
        return descriptor.unit.value
    base = event_type.removesuffix(".overage")
    descriptor = USAGE_EVENT_TYPES.get(base)
    return descriptor.unit.value if descriptor else "unit"


@dataclass
class _Totals:
    quantity: Decimal = Decimal(0)
    estimated_quantity: Decimal = Decimal(0)
    cost_micros: int = 0
    estimated_cost_micros: int = 0
    event_count: int = 0
    late_quantity: Decimal = Decimal(0)
    late_cost_micros: int = 0


def _scope_predicate(
    *, organization_id: uuid.UUID, workspace_id: Optional[uuid.UUID]
):
    if workspace_id is None:
        return and_(
            UsageRollup.organization_id == organization_id,
            UsageRollup.grain == "ORG_TOTAL",
            UsageRollup.event_type != TOTAL_EVENT_TYPE,
        )
    return and_(
        UsageRollup.organization_id == organization_id,
        UsageRollup.grain == "DETAIL",
        UsageRollup.workspace_id == workspace_id,
    )


_AGGREGATES = (
    func.coalesce(func.sum(UsageRollup.quantity), 0),
    func.coalesce(func.sum(UsageRollup.estimated_quantity), 0),
    func.coalesce(func.sum(UsageRollup.cost_micros), 0),
    func.coalesce(func.sum(UsageRollup.estimated_cost_micros), 0),
    func.coalesce(func.sum(UsageRollup.event_count), 0),
    func.coalesce(func.sum(UsageRollup.late_quantity), 0),
    func.coalesce(func.sum(UsageRollup.late_cost_micros), 0),
)


def _lines_for(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    granularity: str,
    start: datetime,
    end: datetime,
) -> list[UsageLine]:
    stmt = (
        select(UsageRollup.event_type, *_AGGREGATES)
        .where(
            _scope_predicate(
                organization_id=organization_id, workspace_id=workspace_id
            ),
            UsageRollup.granularity == granularity,
            UsageRollup.bucket_start >= start,
            UsageRollup.bucket_start < end,
        )
        .group_by(UsageRollup.event_type)
        .order_by(UsageRollup.event_type)
    )

    return [
        UsageLine(
            event_type=row[0],
            unit=_unit_for(row[0]),
            quantity=Decimal(row[1]),
            estimated_quantity=Decimal(row[2]),
            cost_micros=int(row[3]),
            estimated_cost_micros=int(row[4]),
            event_count=int(row[5]),
            late_quantity=Decimal(row[6]),
            late_cost_micros=int(row[7]),
        )
        for row in db.execute(stmt).all()
    ]


def _window(
    db: Session, *, granularity: str, start: datetime
) -> Optional[RollupWindow]:
    return (
        db.execute(
            select(RollupWindow).where(
                RollupWindow.granularity == granularity,
                RollupWindow.bucket_start == start,
            )
        )
        .scalars()
        .first()
    )


def _as_of(
    db: Session, *, granularity: str, start: datetime, end: datetime
) -> Optional[datetime]:
    return db.execute(
        select(func.max(RollupWindow.last_rolled_at)).where(
            RollupWindow.granularity == granularity,
            RollupWindow.bucket_start >= start,
            RollupWindow.bucket_start < end,
        )
    ).scalar_one_or_none()


def summary(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID] = None,
    period: UsagePeriod = UsagePeriod.MONTH,
    at: Optional[datetime] = None,
) -> UsageSummaryResponse:
    moment = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start, end = period_bounds(period, moment)
    granularity = _PERIOD_GRANULARITY[period]

    lines = _lines_for(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        granularity=granularity,
        start=start,
        end=end,
    )

    window = _window(db, granularity=granularity, start=start)
    sealed = bool(window and window.status == "SEALED")

    return UsageSummaryResponse(
        organization_id=organization_id,
        workspace_id=workspace_id,
        period=period,
        period_start=start,
        period_end=end,
        currency=DEFAULT_CURRENCY,
        sealed=sealed,
        sealed_at=window.sealed_at if window else None,
        as_of=(
            window.sealed_at
            if sealed and window
            else _as_of(
                db,
                granularity=rollup_service.HOUR,
                start=start,
                end=end,
            )
        ),
        lines=lines,
        total_cost_micros=sum(line.cost_micros for line in lines),
        estimated_cost_micros=sum(line.estimated_cost_micros for line in lines),
        late_cost_micros=sum(line.late_cost_micros for line in lines),
    )


def _bucket_starts(
    granularity: str, since: datetime, until: datetime
) -> list[datetime]:
    starts: list[datetime] = []
    cursor = rollup_service.bucket_start_for(granularity, since)
    while cursor < until:
        starts.append(cursor)
        cursor = rollup_service.bucket_end(granularity, cursor)
        if len(starts) > MAX_SERIES_BUCKETS:
            break
    return starts


def series(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID] = None,
    granularity: UsageGranularity = UsageGranularity.DAY,
    since: datetime,
    until: Optional[datetime] = None,
    at: Optional[datetime] = None,
) -> UsageSeriesResponse:
    moment = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    end = (until or moment).astimezone(timezone.utc)
    start = since.astimezone(timezone.utc)

    if end <= start:
        raise UsageQueryError("`to` must be after `from`.")

    granularity_name = granularity.value
    starts = _bucket_starts(granularity_name, start, end)
    if len(starts) > MAX_SERIES_BUCKETS:
        raise UsageQueryError(
            f"That range is {len(starts)} {granularity_name} buckets; the maximum is {MAX_SERIES_BUCKETS}."
        )
    if not starts:
        return UsageSeriesResponse(
            organization_id=organization_id,
            workspace_id=workspace_id,
            granularity=granularity,
            range_start=start,
            range_end=end,
            buckets=[],
        )

    aligned_start = starts[0]
    aligned_end = rollup_service.bucket_end(granularity_name, starts[-1])

    stmt = (
        select(UsageRollup.bucket_start, UsageRollup.event_type, *_AGGREGATES)
        .where(
            _scope_predicate(
                organization_id=organization_id, workspace_id=workspace_id
            ),
            UsageRollup.granularity == granularity_name,
            UsageRollup.bucket_start >= aligned_start,
            UsageRollup.bucket_start < aligned_end,
        )
        .group_by(UsageRollup.bucket_start, UsageRollup.event_type)
        .order_by(UsageRollup.bucket_start, UsageRollup.event_type)
    )

    grouped: dict[datetime, list[UsageLine]] = {}
    for row in db.execute(stmt).all():
        bucket = row[0].astimezone(timezone.utc)
        grouped.setdefault(bucket, []).append(
            UsageLine(
                event_type=row[1],
                unit=_unit_for(row[1]),
                quantity=Decimal(row[2]),
                estimated_quantity=Decimal(row[3]),
                cost_micros=int(row[4]),
                estimated_cost_micros=int(row[5]),
                event_count=int(row[6]),
                late_quantity=Decimal(row[7]),
                late_cost_micros=int(row[8]),
            )
        )

    sealed_starts = {
        row[0].astimezone(timezone.utc)
        for row in db.execute(
            select(RollupWindow.bucket_start).where(
                RollupWindow.granularity == granularity_name,
                RollupWindow.status == "SEALED",
                RollupWindow.bucket_start >= aligned_start,
                RollupWindow.bucket_start < aligned_end,
            )
        ).all()
    }

    buckets: list[UsageBucket] = []
    for bucket_start in starts:
        lines = grouped.get(bucket_start, [])
        buckets.append(
            UsageBucket(
                bucket_start=bucket_start,
                bucket_end=rollup_service.bucket_end(granularity_name, bucket_start),
                sealed=bucket_start in sealed_starts,
                lines=lines,
                total_cost_micros=sum(line.cost_micros for line in lines),
                estimated_cost_micros=sum(
                    line.estimated_cost_micros for line in lines
                ),
            )
        )

    return UsageSeriesResponse(
        organization_id=organization_id,
        workspace_id=workspace_id,
        granularity=granularity,
        range_start=aligned_start,
        range_end=aligned_end,
        buckets=buckets,
        total_cost_micros=sum(b.total_cost_micros for b in buckets),
        estimated_cost_micros=sum(b.estimated_cost_micros for b in buckets),
    )


__all__ = [
    "DEFAULT_CURRENCY",
    "UsageQueryError",
    "period_bounds",
    "series",
    "summary",
]
