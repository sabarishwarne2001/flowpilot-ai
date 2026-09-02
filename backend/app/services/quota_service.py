"""ARCH-14 Step 4 — quota tiers, the middle layer of limit resolution."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.usage_events import (
    TOTAL_COST_KEY,
    USAGE_EVENT_TYPES,
    is_limit_key,
    overage_type_for,
    is_overage_type,
)
from app.models.organization import Organization
from app.models.quota_tier import (
    POLICIES_REQUIRING_PRICE,
    OveragePolicy,
    QuotaTier,
    QuotaTierEntry,
    QuotaTierKey,
)
from app.models.spend_limit import SpendLimitPeriod
from app.services import pricing_service
from app.services.pricing_service import PriceUnavailableError

logger = logging.getLogger("app.services.quota")


class QuotaError(Exception):
    """Base class for quota-path refusals."""


class QuotaTierValidationError(QuotaError):
    """A tier was submitted for publication that cannot be published."""


@dataclass(frozen=True)
class TierLimit:
    limit_key: str
    period: SpendLimitPeriod
    max_quantity: Optional[Decimal]
    max_cost_micros: Optional[int]
    overage_policy: str
    overage_price_tier_key: Optional[str]
    grace_quantity: Optional[Decimal]
    quota_tier_id: uuid.UUID
    quota_tier_key: str
    quota_tier_version: int

    @property
    def hard_stop(self) -> bool:
        return self.overage_policy == OveragePolicy.REFUSE.value

    @property
    def bills_overage(self) -> bool:
        return self.overage_policy == OveragePolicy.ALLOW_AND_BILL.value


@dataclass(frozen=True)
class TierEntrySpec:
    limit_key: str
    period: SpendLimitPeriod = SpendLimitPeriod.MONTH
    max_quantity: Optional[Decimal] = None
    max_cost_micros: Optional[int] = None
    overage_policy: str = OveragePolicy.REFUSE.value
    overage_price_tier_key: Optional[str] = None
    grace_quantity: Optional[Decimal] = None
    notes: Optional[str] = None


@dataclass(frozen=True)
class _TierSnapshot:
    id: uuid.UUID
    key: str
    display_name: str
    version: int
    effective_from: datetime
    effective_to: Optional[datetime]
    entries: tuple[TierLimit, ...]

    def covers(self, at: datetime) -> bool:
        if at < self.effective_from:
            return False
        return self.effective_to is None or at < self.effective_to


_cache_lock = threading.Lock()
_cache: Optional[tuple[float, tuple[_TierSnapshot, ...]]] = None


def clear_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clean_decimal_str(val: Optional[Decimal]) -> Optional[str]:
    if val is None:
        return None
    if val == val.to_integral():
        return str(int(val))
    return format(val, "f").rstrip("0").rstrip(".")


def _snapshot(tier: QuotaTier) -> _TierSnapshot:
    return _TierSnapshot(
        id=tier.id,
        key=tier.key,
        display_name=tier.display_name,
        version=tier.version,
        effective_from=_as_utc(tier.effective_from),
        effective_to=_as_utc(tier.effective_to) if tier.effective_to else None,
        entries=tuple(
            TierLimit(
                limit_key=entry.limit_key,
                period=entry.period,
                max_quantity=(
                    Decimal(str(entry.max_quantity))
                    if entry.max_quantity is not None
                    else None
                ),
                max_cost_micros=(
                    int(entry.max_cost_micros)
                    if entry.max_cost_micros is not None
                    else None
                ),
                overage_policy=entry.overage_policy,
                overage_price_tier_key=entry.overage_price_tier_key,
                grace_quantity=(
                    Decimal(str(entry.grace_quantity))
                    if entry.grace_quantity is not None
                    else None
                ),
                quota_tier_id=tier.id,
                quota_tier_key=tier.key,
                quota_tier_version=tier.version,
            )
            for entry in tier.entries
        ),
    )


def _load(db: Session) -> tuple[_TierSnapshot, ...]:
    tiers = (
        db.execute(
            select(QuotaTier)
            .options(selectinload(QuotaTier.entries))
            .where(
                QuotaTier.is_active.is_(True),
                QuotaTier.published_at.is_not(None),
            )
            .order_by(QuotaTier.key, QuotaTier.version.desc())
        )
        .scalars()
        .all()
    )
    return tuple(_snapshot(t) for t in tiers)


def _tiers(db: Session) -> tuple[_TierSnapshot, ...]:
    global _cache
    ttl = float(getattr(settings, "QUOTA_TIER_CACHE_TTL_SECONDS", 300.0) or 0.0)
    if ttl <= 0:
        return _load(db)

    now = time.monotonic()
    with _cache_lock:
        if _cache is not None and (now - _cache[0]) < ttl:
            return _cache[1]

    loaded = _load(db)
    with _cache_lock:
        _cache = (time.monotonic(), loaded)
    return loaded


def _organization_tier_key(db: Session, organization_id: uuid.UUID) -> Optional[str]:
    row = db.execute(
        select(QuotaTier.key)
        .join(Organization, Organization.quota_tier_id == QuotaTier.id)
        .where(Organization.id == organization_id)
    ).first()
    return row[0] if row else None


def _pinned_tier_id(db: Session, organization_id: uuid.UUID) -> Optional[uuid.UUID]:
    """ARCH-15 Step 15.3 — the tier version the live subscription pins."""
    from app.models.billing_account import BillingAccount
    from app.models.subscription import LIVE_SUBSCRIPTION_STATUSES, Subscription

    row = db.execute(
        select(Subscription.quota_tier_id)
        .join(BillingAccount, BillingAccount.id == Subscription.billing_account_id)
        .where(
            BillingAccount.organization_id == organization_id,
            Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES),
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _tier_by_id(db: Session, tier_id: uuid.UUID) -> Optional[_TierSnapshot]:
    """Load one tier version by id, active or superseded."""
    for snapshot in _tiers(db):
        if snapshot.id == tier_id:
            return snapshot

    tier = db.execute(
        select(QuotaTier)
        .options(selectinload(QuotaTier.entries))
        .where(QuotaTier.id == tier_id)
    ).scalar_one_or_none()
    return _snapshot(tier) if tier is not None else None


def resolve_tier(
    db: Session, *, organization_id: uuid.UUID, at: Optional[datetime] = None
) -> Optional[_TierSnapshot]:
    pinned_id = _pinned_tier_id(db, organization_id)
    if pinned_id is not None:
        pinned = _tier_by_id(db, pinned_id)
        if pinned is not None:
            return pinned
        logger.error(
            "quota.pinned_tier_version_missing",
            extra={
                "organization_id": str(organization_id),
                "quota_tier_id": str(pinned_id),
            },
        )

    key = _organization_tier_key(db, organization_id)
    if key is None:
        return None

    moment = _as_utc(at or datetime.now(timezone.utc))
    covering = [t for t in _tiers(db) if t.key == key and t.covers(moment)]
    if not covering:
        logger.warning(
            "quota.no_tier_version_in_force",
            extra={
                "organization_id": str(organization_id),
                "tier_key": key,
                "at": moment.isoformat(),
            },
        )
        return None
    if len(covering) > 1:
        covering.sort(key=lambda t: t.version, reverse=True)
        logger.error(
            "quota.overlapping_tier_versions",
            extra={
                "tier_key": key,
                "versions": [t.version for t in covering],
                "chosen": covering[0].version,
            },
        )
    return covering[0]


def tier_limits(
    db: Session,
    *,
    organization_id: uuid.UUID,
    limit_key: str,
    at: Optional[datetime] = None,
) -> list[TierLimit]:
    tier = resolve_tier(db, organization_id=organization_id, at=at)
    if tier is None:
        return []
    return [entry for entry in tier.entries if entry.limit_key == limit_key]


def tier_limit_for(
    db: Session,
    *,
    organization_id: uuid.UUID,
    limit_key: str,
    period: SpendLimitPeriod,
    at: Optional[datetime] = None,
) -> Optional[TierLimit]:
    for entry in tier_limits(
        db, organization_id=organization_id, limit_key=limit_key, at=at
    ):
        if entry.period == period:
            return entry
    return None


@dataclass(frozen=True)
class OverageOutcome:
    billed: bool
    overage_quantity: Decimal = Decimal(0)
    cost_micros: int = 0
    event_type: Optional[str] = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "billed": self.billed,
            "overage_quantity": str(self.overage_quantity),
            "cost_micros": self.cost_micros,
            "event_type": self.event_type,
            "reason": self.reason,
        }


_NOT_BILLED_UNDER = OverageOutcome(billed=False, reason="within_ceiling")


def bill_overage_if_any(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    quantity: Any,
    workspace_id: Optional[uuid.UUID] = None,
    occurred_at: Optional[datetime] = None,
    idempotency_key: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[uuid.UUID] = None,
) -> OverageOutcome:
    from app.services import spend_control_service as spend
    from app.services import usage_service

    if is_overage_type(event_type):
        return OverageOutcome(billed=False, reason="already_an_overage_row")

    settled_quantity = Decimal(str(quantity))
    if settled_quantity <= 0:
        return _NOT_BILLED_UNDER

    moment = _as_utc(occurred_at or datetime.now(timezone.utc))

    override = spend.explicit_limits(
        db, organization_id=organization_id, limit_key=event_type, lock=False
    )
    if override:
        return OverageOutcome(billed=False, reason="explicit_override_governs")

    outcomes: list[OverageOutcome] = []
    for entry in tier_limits(
        db, organization_id=organization_id, limit_key=event_type, at=moment
    ):
        if not entry.bills_overage:
            continue
        if entry.max_quantity is None:
            continue

        since = spend.period_start(entry.period, now=moment)
        totals = usage_service.usage_totals_bounded(
            db,
            organization_id=organization_id,
            since=since,
            event_types=[event_type],
            now=moment,
        ).get(event_type, (Decimal(0), 0))
        total_after = totals[0]

        allowance = entry.max_quantity + (entry.grace_quantity or Decimal(0))
        above = total_after - allowance
        if above <= 0:
            continue

        overage_quantity = min(settled_quantity, above)
        if overage_quantity <= 0:
            continue

        overage_event = overage_type_for(event_type)
        try:
            price = pricing_service.resolve(
                db,
                event_type=overage_event,
                provider=provider or "internal",
                model=model,
                at=moment,
                tier_key=entry.overage_price_tier_key,
            )
        except PriceUnavailableError:
            logger.critical(
                "quota.overage_unpriced",
                extra={
                    "organization_id": str(organization_id),
                    "event_type": overage_event,
                    "tier_key": entry.overage_price_tier_key,
                    "quota_tier": f"{entry.quota_tier_key}/v{entry.quota_tier_version}",
                },
            )
            outcomes.append(
                OverageOutcome(
                    billed=False,
                    overage_quantity=overage_quantity,
                    event_type=overage_event,
                    reason="overage_price_unavailable",
                )
            )
            continue

        cost = price.cost_micros(overage_quantity)
        overage_details = {
            "model": model,
            "overage_of": event_type,
            "quota_tier": entry.quota_tier_key,
            "quota_tier_version": entry.quota_tier_version,
            "quota_period": entry.period.value,
            "ceiling_quantity": _clean_decimal_str(entry.max_quantity),
            "grace_quantity": _clean_decimal_str(entry.grace_quantity or Decimal(0)),
            "overage_policy": entry.overage_policy,
            **price.as_details(),
        }

        savepoint = db.begin_nested()
        try:
            usage_service.record_usage(
                db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                event_type=overage_event,
                quantity=overage_quantity,
                cost_micros=cost,
                price_book_id=price.price_book_id,
                unit_price_micros=price.unit_price_micros,
                provider=provider,
                resource_type=resource_type,
                resource_id=resource_id,
                occurred_at=moment,
                idempotency_key=(
                    f"{idempotency_key}:overage" if idempotency_key else None
                ),
                details=overage_details,
            )
            savepoint.commit()
            logger.info(
                "quota.overage_billed",
                extra={
                    "organization_id": str(organization_id),
                    "event_type": overage_event,
                    "quantity": str(overage_quantity),
                    "cost_micros": cost,
                    "quota_tier": entry.quota_tier_key,
                },
            )
            outcomes.append(
                OverageOutcome(
                    billed=True,
                    overage_quantity=overage_quantity,
                    cost_micros=cost,
                    event_type=overage_event,
                    reason="billed",
                )
            )
        except IntegrityError:
            savepoint.rollback()
            logger.info(
                "quota.overage_already_billed",
                extra={
                    "idempotency_key": f"{idempotency_key}:overage" if idempotency_key else None,
                    "organization_id": str(organization_id),
                },
            )
            outcomes.append(
                OverageOutcome(
                    billed=True,
                    overage_quantity=overage_quantity,
                    cost_micros=cost,
                    event_type=overage_event,
                    reason="already_billed",
                )
            )

    if not outcomes:
        return _NOT_BILLED_UNDER
    billed = [o for o in outcomes if o.billed]
    return billed[0] if billed else outcomes[0]


@dataclass(frozen=True)
class QuotaStatus:
    limit_key: str
    period: str
    source: str
    max_quantity: Optional[Decimal]
    max_cost_micros: Optional[int]
    current_quantity: Decimal
    current_cost_micros: int
    overage_policy: str
    grace_quantity: Optional[Decimal]
    hard_stop: bool
    quota_tier_key: Optional[str]
    quota_tier_version: Optional[int]
    period_start: datetime
    resets_at: datetime

    @property
    def remaining_quantity(self) -> Optional[Decimal]:
        if self.max_quantity is None:
            return None
        return max(Decimal(0), self.max_quantity - self.current_quantity)

    @property
    def remaining_cost_micros(self) -> Optional[int]:
        if self.max_cost_micros is None:
            return None
        return max(0, self.max_cost_micros - self.current_cost_micros)


def quota_status(
    db: Session,
    *,
    organization_id: uuid.UUID,
    at: Optional[datetime] = None,
) -> list[QuotaStatus]:
    from app.services import spend_control_service as spend
    from app.services import usage_service

    moment = _as_utc(at or datetime.now(timezone.utc))
    tier = resolve_tier(db, organization_id=organization_id, at=moment)

    keys = [TOTAL_COST_KEY] + [
        name for name in sorted(USAGE_EVENT_TYPES) if is_limit_key(name)
    ]

    statuses: list[QuotaStatus] = []
    for limit_key in keys:
        for limit in spend.effective_limits(
            db, organization_id=organization_id, limit_key=limit_key, lock=False
        ):
            since = spend.period_start(limit.period, now=moment)
            if limit_key == TOTAL_COST_KEY:
                current_qty = Decimal(0)
                current_cost = usage_service.total_cost_micros_bounded(
                    db, organization_id=organization_id, since=since, now=moment
                )
            else:
                current_qty, current_cost = usage_service.usage_totals_bounded(
                    db,
                    organization_id=organization_id,
                    since=since,
                    event_types=[limit_key],
                    now=moment,
                ).get(limit_key, (Decimal(0), 0))

            statuses.append(
                QuotaStatus(
                    limit_key=limit.limit_key,
                    period=limit.period.value,
                    source=getattr(limit, "source", None)
                    or ("PLATFORM_DEFAULT" if limit.is_default else "ORGANIZATION"),
                    max_quantity=limit.max_quantity,
                    max_cost_micros=limit.max_cost_micros,
                    current_quantity=current_qty,
                    current_cost_micros=current_cost,
                    overage_policy=getattr(
                        limit, "overage_policy", OveragePolicy.REFUSE.value
                    ),
                    grace_quantity=getattr(limit, "grace_quantity", None),
                    hard_stop=limit.hard_stop,
                    quota_tier_key=tier.key if tier else None,
                    quota_tier_version=tier.version if tier else None,
                    period_start=since,
                    resets_at=spend.period_end(limit.period, now=moment),
                )
            )

    return statuses


def _validate(
    db: Session, *, entries: Sequence[TierEntrySpec], effective_from: datetime
) -> list[TierEntrySpec]:
    if not entries:
        raise QuotaTierValidationError("A tier with no entries constrains nothing.")

    seen: set[tuple[str, str]] = set()
    validated: list[TierEntrySpec] = []

    for spec in entries:
        if not is_limit_key(spec.limit_key):
            raise QuotaTierValidationError(
                f"'{spec.limit_key}' is neither the wildcard '{TOTAL_COST_KEY}' nor a billable usage event type."
            )
        if spec.max_quantity is None and spec.max_cost_micros is None:
            raise QuotaTierValidationError(
                f"Entry for '{spec.limit_key}' has no ceiling in either dimension."
            )
        if spec.overage_policy not in {p.value for p in OveragePolicy}:
            raise QuotaTierValidationError(
                f"Unknown overage policy {spec.overage_policy!r}."
            )

        key = (spec.limit_key, spec.period.value)
        if key in seen:
            raise QuotaTierValidationError(f"Duplicate entry {key!r}.")
        seen.add(key)

        if spec.overage_policy in POLICIES_REQUIRING_PRICE:
            if not spec.overage_price_tier_key:
                raise QuotaTierValidationError(
                    f"'{spec.limit_key}' is ALLOW_AND_BILL with no overage_price_tier_key."
                )
            if spec.limit_key != TOTAL_COST_KEY:
                overage_event = overage_type_for(spec.limit_key)
                descriptor = USAGE_EVENT_TYPES.get(overage_event)
                if descriptor is None:
                    raise QuotaTierValidationError(
                        f"'{overage_event}' is not in the usage vocabulary."
                    )
                priced = False
                for provider in _known_providers():
                    try:
                        pricing_service.resolve(
                            db,
                            event_type=overage_event,
                            provider=provider,
                            model=None,
                            at=effective_from,
                            tier_key=spec.overage_price_tier_key,
                        )
                        priced = True
                        break
                    except PriceUnavailableError:
                        continue
                if not priced:
                    raise QuotaTierValidationError(
                        f"No published price book entry prices '{overage_event}' at tier_key "
                        f"{spec.overage_price_tier_key!r} as of {effective_from.isoformat()}."
                    )

        validated.append(spec)

    return validated


def _known_providers() -> list[str]:
    return sorted(
        {
            descriptor.default_provider
            for descriptor in USAGE_EVENT_TYPES.values()
            if descriptor.default_provider
        }
        | {"groq", "gemini", "internal"}
    )


def publish_tier(
    db: Session,
    *,
    key: str,
    display_name: str,
    version: int,
    effective_from: datetime,
    entries: Sequence[TierEntrySpec],
    published_by_user_id: Optional[uuid.UUID] = None,
    notes: Optional[str] = None,
    close_predecessor: bool = True,
) -> QuotaTier:
    moment = _as_utc(effective_from)
    known = {t.value for t in QuotaTierKey}
    if key not in known:
        logger.warning(
            "quota.unknown_tier_key",
            extra={"key": key, "known": sorted(known)},
        )

    validated = _validate(db, entries=entries, effective_from=moment)

    clash = db.execute(
        select(QuotaTier.id).where(
            QuotaTier.key == key, QuotaTier.version == version
        )
    ).first()
    if clash is not None:
        raise QuotaTierValidationError(
            f"Tier {key} version {version} already exists."
        )

    tier = QuotaTier(
        key=key,
        display_name=display_name,
        version=version,
        effective_from=moment,
        effective_to=None,
        is_active=False,
        notes=notes,
    )
    db.add(tier)
    db.flush([tier])

    for spec in validated:
        db.add(
            QuotaTierEntry(
                quota_tier_id=tier.id,
                limit_key=spec.limit_key,
                period=spec.period,
                max_quantity=spec.max_quantity,
                max_cost_micros=spec.max_cost_micros,
                overage_policy=spec.overage_policy,
                overage_price_tier_key=spec.overage_price_tier_key,
                grace_quantity=spec.grace_quantity,
                notes=spec.notes,
            )
        )
    db.flush()

    if close_predecessor:
        predecessor = (
            db.execute(
                select(QuotaTier)
                .where(
                    QuotaTier.key == key,
                    QuotaTier.is_active.is_(True),
                    QuotaTier.published_at.is_not(None),
                    QuotaTier.effective_to.is_(None),
                    QuotaTier.effective_from < moment,
                )
                .order_by(QuotaTier.version.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if predecessor is not None:
            predecessor.effective_to = moment
            db.flush([predecessor])

    tier.published_at = datetime.now(timezone.utc)
    tier.published_by_user_id = published_by_user_id
    tier.is_active = True
    db.flush([tier])

    clear_cache()
    logger.info(
        "quota.tier_published",
        extra={
            "quota_tier_id": str(tier.id),
            "key": key,
            "version": version,
            "entries": len(validated),
            "effective_from": moment.isoformat(),
        },
    )
    return tier


def assign_tier(
    db: Session,
    *,
    organization_id: uuid.UUID,
    tier_key: str,
    at: Optional[datetime] = None,
) -> QuotaTier:
    moment = _as_utc(at or datetime.now(timezone.utc))
    tier = (
        db.execute(
            select(QuotaTier)
            .where(
                QuotaTier.key == tier_key,
                QuotaTier.is_active.is_(True),
                QuotaTier.published_at.is_not(None),
                QuotaTier.effective_from <= moment,
            )
            .order_by(QuotaTier.version.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if tier is None:
        raise QuotaTierValidationError(
            f"No published tier '{tier_key}' is in force at {moment.isoformat()}."
        )

    organization = db.get(Organization, organization_id)
    if organization is None:
        raise QuotaError(f"organization {organization_id} not found")

    organization.quota_tier_id = tier.id
    db.flush([organization])
    logger.info(
        "quota.tier_assigned",
        extra={
            "organization_id": str(organization_id),
            "tier": f"{tier.key}/v{tier.version}",
        },
    )
    return tier


def list_published_tiers(
    db: Session,
    *,
    at: Optional[datetime] = None,
) -> list[_TierSnapshot]:
    """Every tier a customer could currently be sold, newest version per key."""
    moment = _as_utc(at or datetime.now(timezone.utc))

    live: dict[str, _TierSnapshot] = {}
    for tier in _tiers(db):
        if tier.effective_from and _as_utc(tier.effective_from) > moment:
            continue
        if tier.effective_to and _as_utc(tier.effective_to) <= moment:
            continue

        existing = live.get(tier.key)
        if existing is None or tier.version > existing.version:
            live[tier.key] = tier

    rank = {"free": 0, "developer": 1, "business": 2, "enterprise": 3}
    return sorted(live.values(), key=lambda t: (rank.get(t.key, 99), t.key))


__all__ = [
    "OverageOutcome",
    "OveragePolicy",
    "QuotaError",
    "QuotaStatus",
    "QuotaTierValidationError",
    "TierEntrySpec",
    "TierLimit",
    "assign_tier",
    "bill_overage_if_any",
    "clear_cache",
    "list_published_tiers",
    "publish_tier",
    "quota_status",
    "resolve_tier",
    "tier_limit_for",
    "tier_limits",
]
