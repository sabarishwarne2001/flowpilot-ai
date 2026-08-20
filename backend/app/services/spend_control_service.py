"""ARCH-10 Step 3, ARCH-14 Step 14.3 & ARCH-14 Step 14.4 — per-tenant spend ceilings & quota tiers."""

from __future__ import annotations

import calendar
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterator, Mapping, Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    SpendLimitExceededError,
    SpendLimitMisconfiguredError,
)
from app.core.principal import Principal, get_current_principal
from app.core.usage_events import TOTAL_COST_KEY, USAGE_EVENT_TYPES, is_limit_key
from app.models.audit_log import (
    AuditAction,
    AuditLog,
    AuditOutcome,
    AuditResourceType,
)
from app.models.spend_limit import SpendLimit, SpendLimitPeriod
from app.services import audit_service, quota_service, usage_service

logger = logging.getLogger("app.services.spend_control")

_ADVISORY_LOCK_NAMESPACE: int = 0x5350_4E44


@dataclass(frozen=True)
class EffectiveLimit:
    limit_key: str
    period: SpendLimitPeriod
    max_quantity: Optional[Decimal]
    max_cost_micros: Optional[int]
    hard_stop: bool
    is_default: bool
    limit_id: Optional[uuid.UUID] = None
    source: str = "ORGANIZATION"
    overage_policy: str = "REFUSE"
    overage_price_tier_key: Optional[str] = None
    grace_quantity: Optional[Decimal] = None
    quota_tier_id: Optional[uuid.UUID] = None
    quota_tier_key: Optional[str] = None
    quota_tier_version: Optional[int] = None


@dataclass
class UsageGuard:
    db: Session
    organization_id: uuid.UUID
    event_type: str
    workspace_id: Optional[uuid.UUID] = None
    job_id: Optional[uuid.UUID] = None
    resource_type: Optional[str] = None
    resource_id: Optional[uuid.UUID] = None
    idempotency_key: Optional[str] = None
    principal: Optional[Principal] = None
    recorded: list[Any] = field(default_factory=list)

    def record(
        self,
        *,
        quantity: Any,
        cost_micros: Optional[int] = None,
        provider: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
        occurred_at: Optional[datetime] = None,
    ) -> Any:
        event = usage_service.record_usage(
            self.db,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            event_type=self.event_type,
            quantity=quantity,
            cost_micros=cost_micros,
            provider=provider,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
            job_id=self.job_id,
            idempotency_key=self.idempotency_key,
            occurred_at=occurred_at,
            details=details,
            principal=self.principal,
        )

        quota_service.bill_overage_if_any(
            self.db,
            organization_id=self.organization_id,
            event_type=self.event_type,
            quantity=quantity,
            workspace_id=self.workspace_id,
            occurred_at=occurred_at,
            idempotency_key=self.idempotency_key,
            provider=provider,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
        )

        self.recorded.append(event)
        return event


def period_start(period: SpendLimitPeriod, *, now: Optional[datetime] = None) -> datetime:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if period is SpendLimitPeriod.DAY:
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def period_end(period: SpendLimitPeriod, *, now: Optional[datetime] = None) -> datetime:
    start = period_start(period, now=now)
    if period is SpendLimitPeriod.DAY:
        return start + timedelta(days=1)
    days = calendar.monthrange(start.year, start.month)[1]
    return start + timedelta(days=days)


def _platform_defaults(limit_key: str) -> list[EffectiveLimit]:
    defaults: list[EffectiveLimit] = []

    if limit_key == TOTAL_COST_KEY:
        if settings.SPEND_DEFAULT_MONTHLY_COST_MICROS is not None:
            defaults.append(
                EffectiveLimit(
                    limit_key=TOTAL_COST_KEY,
                    period=SpendLimitPeriod.MONTH,
                    max_quantity=None,
                    max_cost_micros=settings.SPEND_DEFAULT_MONTHLY_COST_MICROS,
                    hard_stop=True,
                    is_default=True,
                    source="PLATFORM_DEFAULT",
                )
            )
        if settings.SPEND_DEFAULT_DAILY_COST_MICROS is not None:
            defaults.append(
                EffectiveLimit(
                    limit_key=TOTAL_COST_KEY,
                    period=SpendLimitPeriod.DAY,
                    max_quantity=None,
                    max_cost_micros=settings.SPEND_DEFAULT_DAILY_COST_MICROS,
                    hard_stop=True,
                    is_default=True,
                    source="PLATFORM_DEFAULT",
                )
            )
        return defaults

    configured = settings.spend_default_quantities().get(limit_key)
    if configured is not None:
        defaults.append(
            EffectiveLimit(
                limit_key=limit_key,
                period=SpendLimitPeriod.MONTH,
                max_quantity=Decimal(str(configured)),
                max_cost_micros=None,
                hard_stop=True,
                is_default=True,
                source="PLATFORM_DEFAULT",
            )
        )
    return defaults


def _lock_organization(db: Session, organization_id: uuid.UUID) -> None:
    db.execute(
        select(
            func.pg_advisory_xact_lock(
                _ADVISORY_LOCK_NAMESPACE,
                func.hashtext(str(organization_id)),
            )
        )
    )


def explicit_limits(
    db: Session,
    *,
    organization_id: uuid.UUID,
    limit_key: str,
    lock: bool = True,
) -> list[EffectiveLimit]:
    stmt = select(SpendLimit).where(
        SpendLimit.organization_id == organization_id,
        SpendLimit.limit_key == limit_key,
        SpendLimit.is_active.is_(True),
    )
    if lock:
        stmt = stmt.with_for_update()

    return [
        EffectiveLimit(
            limit_key=row.limit_key,
            period=row.period,
            max_quantity=row.max_quantity,
            max_cost_micros=row.max_cost_micros,
            hard_stop=row.hard_stop,
            is_default=False,
            limit_id=row.id,
            source="ORGANIZATION",
        )
        for row in db.execute(stmt).scalars().all()
    ]


def effective_limits(
    db: Session,
    *,
    organization_id: uuid.UUID,
    limit_key: str,
    lock: bool = True,
) -> list[EffectiveLimit]:
    if not is_limit_key(limit_key):
        raise SpendLimitMisconfiguredError(
            f"'{limit_key}' is neither the wildcard '{TOTAL_COST_KEY}' nor a billable usage event type."
        )

    explicit = explicit_limits(
        db, organization_id=organization_id, limit_key=limit_key, lock=lock
    )
    if explicit:
        return explicit

    tier = quota_service.tier_limits(
        db, organization_id=organization_id, limit_key=limit_key
    )
    if tier:
        if lock:
            _lock_organization(db, organization_id)
        return [
            EffectiveLimit(
                limit_key=entry.limit_key,
                period=entry.period,
                max_quantity=entry.max_quantity,
                max_cost_micros=entry.max_cost_micros,
                hard_stop=entry.hard_stop,
                is_default=False,
                limit_id=None,
                source="TIER",
                overage_policy=entry.overage_policy,
                overage_price_tier_key=entry.overage_price_tier_key,
                grace_quantity=entry.grace_quantity,
                quota_tier_id=entry.quota_tier_id,
                quota_tier_key=entry.quota_tier_key,
                quota_tier_version=entry.quota_tier_version,
            )
            for entry in tier
        ]

    if lock:
        _lock_organization(db, organization_id)
    return _platform_defaults(limit_key)


def _denial_already_audited_this_hour(
    db: Session, *, organization_id: uuid.UUID, limit_key: str
) -> bool:
    window_start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    stmt = (
        select(AuditLog.id)
        .where(
            AuditLog.organization_id == organization_id,
            AuditLog.resource_type == AuditResourceType.SPEND_LIMIT,
            AuditLog.action == AuditAction.EXCEEDED,
            AuditLog.created_at >= window_start,
            AuditLog.details["limit_key"].astext == limit_key,
        )
        .limit(1)
    )
    return db.execute(stmt).first() is not None


def _audit_denial(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    limit: EffectiveLimit,
    current: Decimal | int,
    requested: Decimal | int,
    dimension: str,
    principal: Optional[Principal],
) -> None:
    logger.warning(
        "spend.denied",
        extra={
            "organization_id": str(organization_id),
            "limit_key": limit.limit_key,
            "period": limit.period.value,
            "dimension": dimension,
            "current": str(current),
            "requested": str(requested),
            "ceiling": str(limit.max_quantity or limit.max_cost_micros),
            "is_default": limit.is_default,
        },
    )

    try:
        if _denial_already_audited_this_hour(
            db, organization_id=organization_id, limit_key=limit.limit_key
        ):
            return
    except Exception:
        logger.exception("spend.denial_dedupe_check_failed")

    audit_service.record_independently(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        principal=principal or get_current_principal(),
        resource_type=AuditResourceType.SPEND_LIMIT,
        resource_id=limit.limit_id,
        action=AuditAction.EXCEEDED,
        outcome=AuditOutcome.DENIED,
        details={
            "limit_key": limit.limit_key,
            "period": limit.period.value,
            "dimension": dimension,
            "ceiling": str(limit.max_quantity or limit.max_cost_micros),
            "current": str(current),
            "requested": str(requested),
            "source": limit.source,
            "note": "aggregated: one row per organization per limit per hour",
        },
    )


def ensure_within_limits(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    quantity: Any,
    cost_micros: Optional[int] = None,
    workspace_id: Optional[uuid.UUID] = None,
    principal: Optional[Principal] = None,
) -> None:
    if event_type not in USAGE_EVENT_TYPES:
        raise SpendLimitMisconfiguredError(
            f"'{event_type}' is not a known usage event type."
        )

    requested_qty = Decimal(str(quantity))
    requested_cost = int(cost_micros or 0)

    checks: list[EffectiveLimit] = []
    checks.extend(
        effective_limits(db, organization_id=organization_id, limit_key=event_type)
    )
    if requested_cost > 0 or USAGE_EVENT_TYPES[event_type].billable:
        checks.extend(
            effective_limits(
                db, organization_id=organization_id, limit_key=TOTAL_COST_KEY
            )
        )

    for limit in checks:
        since = period_start(limit.period)

        if limit.limit_key == TOTAL_COST_KEY:
            current_cost = usage_service.total_cost_micros_bounded(
                db, organization_id=organization_id, since=since
            )
            current_qty = Decimal(0)
        else:
            totals = usage_service.usage_totals_bounded(
                db,
                organization_id=organization_id,
                since=since,
                event_types=[limit.limit_key],
            ).get(limit.limit_key, (Decimal(0), 0))
            current_qty, current_cost = totals

        quantity_ceiling = limit.max_quantity
        if quantity_ceiling is not None and limit.grace_quantity:
            quantity_ceiling = quantity_ceiling + limit.grace_quantity

        if (
            quantity_ceiling is not None
            and current_qty + requested_qty > quantity_ceiling
        ):
            _deny_or_warn(
                db,
                limit=limit,
                dimension="quantity",
                current=current_qty,
                requested=requested_qty,
                ceiling=quantity_ceiling,
                organization_id=organization_id,
                workspace_id=workspace_id,
                principal=principal,
            )

        if (
            limit.max_cost_micros is not None
            and current_cost + requested_cost > limit.max_cost_micros
        ):
            _deny_or_warn(
                db,
                limit=limit,
                dimension="cost_micros",
                current=current_cost,
                requested=requested_cost,
                ceiling=limit.max_cost_micros,
                organization_id=organization_id,
                workspace_id=workspace_id,
                principal=principal,
            )


def _deny_or_warn(
    db: Session,
    *,
    limit: EffectiveLimit,
    dimension: str,
    current: Decimal | int,
    requested: Decimal | int,
    ceiling: Decimal | int,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    principal: Optional[Principal],
) -> None:
    _audit_denial(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        limit=limit,
        current=current,
        requested=requested,
        dimension=dimension,
        principal=principal,
    )
    if not limit.hard_stop:
        return
    raise SpendLimitExceededError(
        limit_key=limit.limit_key,
        period=limit.period.value,
        dimension=dimension,
        ceiling=str(ceiling),
        current=str(current),
        requested=str(requested),
        resets_at=period_end(limit.period),
        is_platform_default=limit.is_default,
    )


@contextmanager
def guard_usage(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    estimated_quantity: Any,
    estimated_cost_micros: Optional[int] = None,
    workspace_id: Optional[uuid.UUID] = None,
    job_id: Optional[uuid.UUID] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[uuid.UUID] = None,
    idempotency_key: Optional[str] = None,
    principal: Optional[Principal] = None,
) -> Iterator[UsageGuard]:
    ensure_within_limits(
        db,
        organization_id=organization_id,
        event_type=event_type,
        quantity=estimated_quantity,
        cost_micros=estimated_cost_micros,
        workspace_id=workspace_id,
        principal=principal,
    )
    guard = UsageGuard(
        db=db,
        organization_id=organization_id,
        event_type=event_type,
        workspace_id=workspace_id,
        job_id=job_id,
        resource_type=resource_type,
        resource_id=resource_id,
        idempotency_key=idempotency_key,
        principal=principal,
    )
    yield guard
    if not guard.recorded:
        logger.warning(
            "spend.guard_recorded_nothing",
            extra={
                "organization_id": str(organization_id),
                "event_type": event_type,
                "job_id": str(job_id) if job_id else None,
            },
        )


def set_limit(
    db: Session,
    *,
    organization_id: uuid.UUID,
    limit_key: str,
    period: SpendLimitPeriod,
    max_quantity: Optional[Decimal] = None,
    max_cost_micros: Optional[int] = None,
    hard_stop: bool = True,
    note: Optional[str] = None,
    principal: Optional[Principal] = None,
) -> SpendLimit:
    if not is_limit_key(limit_key):
        raise SpendLimitMisconfiguredError(
            f"'{limit_key}' is not a valid limit key."
        )
    if max_quantity is None and max_cost_micros is None:
        raise SpendLimitMisconfiguredError(
            "A limit must set max_quantity, max_cost_micros, or both."
        )

    existing = db.execute(
        select(SpendLimit.id)
        .where(
            SpendLimit.organization_id == organization_id,
            SpendLimit.limit_key == limit_key,
            SpendLimit.period == period,
            SpendLimit.is_active.is_(True),
        )
        .with_for_update()
    ).scalars().all()

    if existing:
        db.execute(
            update(SpendLimit)
            .where(SpendLimit.id.in_(existing))
            .values(is_active=False)
        )
        db.flush()

    limit = SpendLimit(
        organization_id=organization_id,
        limit_key=limit_key,
        period=period,
        max_quantity=max_quantity,
        max_cost_micros=max_cost_micros,
        hard_stop=hard_stop,
        is_active=True,
        note=note,
    )
    db.add(limit)
    db.flush([limit])

    audit_service.record(
        db,
        organization_id=organization_id,
        principal=principal or get_current_principal(),
        resource_type=AuditResourceType.SPEND_LIMIT,
        resource_id=limit.id,
        action=AuditAction.UPDATED if existing else AuditAction.CREATED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "limit_key": limit_key,
            "period": period.value,
            "max_quantity": str(max_quantity) if max_quantity is not None else None,
            "max_cost_micros": max_cost_micros,
            "hard_stop": hard_stop,
            "replaced": [str(row_id) for row_id in existing],
        },
    )
    return limit