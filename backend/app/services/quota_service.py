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
from app.core import entitlements, plan_limits
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
class TierCommercials:
    """ARCH-30 Tranche 2 (T5-F1). The price terms a tier version is published with.

    `publish_tier` had no parameter for these, so `seed_quota_tiers.py`
    defined `COMMERCIALS` and never passed it anywhere. Every published
    version — v2 included — reached the database with NULL prices, and a
    published tier is immutable, so the only repair is a new version.

    Validated here against the same all-or-nothing rule as
    `ck_quota_tiers_price_complete`, so a malformed price is a readable
    `QuotaTierValidationError` before the insert rather than a CHECK violation
    after it.
    """

    unit_amount_micros: int
    currency: str
    billing_interval: str
    gateway_price_id: Optional[str] = None

    def violation(self) -> Optional[str]:
        if self.unit_amount_micros < 0:
            return "unit_amount_micros must be >= 0."
        if len(self.currency or "") != 3 or self.currency != self.currency.upper():
            return f"currency must be a 3-letter upper-case ISO code, got {self.currency!r}."
        if self.billing_interval not in ("month", "year"):
            return f"billing_interval must be 'month' or 'year', got {self.billing_interval!r}."
        if self.unit_amount_micros > 0 and not self.gateway_price_id:
            return (
                "A paid tier needs gateway_price_id. Without it checkout would "
                "have to invent what it is selling; publish it unpriced instead."
            )
        return None


@dataclass(frozen=True)
class _TierSnapshot:
    id: uuid.UUID
    key: str
    display_name: str
    version: int
    effective_from: datetime
    effective_to: Optional[datetime]
    entries: tuple[TierLimit, ...]

    # ARCH-29 Tranche 2. Carried on the snapshot because `list_published_tiers`
    # returns snapshots, not ORM rows, and `list_plans` reads only what the
    # snapshot exposes. Omitted here, the columns would exist in the database,
    # be correctly populated by the seed, and still render as
    # "Contact us for pricing" — the original bug surviving its own fix.
    unit_amount_micros: Optional[int] = None
    currency: Optional[str] = None
    billing_interval: Optional[str] = None
    gateway_price_id: Optional[str] = None

    @property
    def is_priced(self) -> bool:
        return self.unit_amount_micros is not None

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
        unit_amount_micros=(
            int(tier.unit_amount_micros)
            if tier.unit_amount_micros is not None
            else None
        ),
        currency=tier.currency,
        billing_interval=tier.billing_interval,
        gateway_price_id=tier.gateway_price_id,
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


#: The plan whose metered allowance is pooled per owner account (campaign session 1).
FREE_TIER_KEY: str = "free"


def _live_seats(db: Session, organization_id: uuid.UUID) -> Optional[int]:
    """Seats on the organization's live subscription, or None without one."""
    from app.models.billing_account import BillingAccount
    from app.models.subscription import LIVE_SUBSCRIPTION_STATUSES, Subscription

    return db.execute(
        select(Subscription.seats_purchased)
        .join(BillingAccount, BillingAccount.id == Subscription.billing_account_id)
        .where(
            BillingAccount.organization_id == organization_id,
            Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES),
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def seat_factor(db: Session, *, organization_id: uuid.UUID, tier: Optional[_TierSnapshot] = None) -> int:
    """How many times a per-seat allowance the organization holds (campaign session 1).

    A paid plan is priced per seat, so its metered allowances are per seat and
    pooled across the organization: the tier's number times the seats the live
    subscription holds. Free (and any unpriced tier) is per organization: 1.
    """
    if tier is None:
        tier = resolve_tier(db, organization_id=organization_id)
    if tier is None or not tier.unit_amount_micros:
        return 1
    seats = _live_seats(db, organization_id)
    return max(1, int(seats or 1))


def _scaled(entry: TierLimit, factor: int) -> TierLimit:
    from dataclasses import replace

    return replace(
        entry,
        max_quantity=(entry.max_quantity * factor if entry.max_quantity is not None else None),
        max_cost_micros=(entry.max_cost_micros * factor if entry.max_cost_micros is not None else None),
    )


def tier_limits(
    db: Session,
    *,
    organization_id: uuid.UUID,
    limit_key: str,
    at: Optional[datetime] = None,
) -> list[TierLimit]:
    """The tier rows for `limit_key`, as they apply to this organization.

    Campaign session 1: a metered row of a per-seat plan is multiplied by the
    seats the subscription holds (`seat_factor`); entitlements and static plan
    limits are never scaled. Every reader of an allowance (enforcement, overage
    billing, the usage screens) comes through here, so they cannot disagree.
    """
    tier = resolve_tier(db, organization_id=organization_id, at=at)
    if tier is None:
        return []
    entries = [entry for entry in tier.entries if entry.limit_key == limit_key]
    if not entries or not is_limit_key(limit_key):
        return entries
    factor = seat_factor(db, organization_id=organization_id, tier=tier)
    if factor == 1:
        return entries
    return [_scaled(entry, factor) for entry in entries]


def usage_pool(db: Session, *, organization_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
    """The organizations whose usage counts against this one's allowance.

    Campaign session 1. The Free allowance belongs to the owner ACCOUNT: every Free
    organization the same person owns (archived ones included, so archiving and
    recreating resets nothing) draws on one pool. A paid organization is its own
    pool. Sorted, so the first member is a stable lock anchor for the whole pool.
    """
    from app.models.organization import MembershipStatus, OrganizationMember, OrganizationRole

    tier = resolve_tier(db, organization_id=organization_id)
    if tier is None or tier.key != FREE_TIER_KEY:
        return (organization_id,)
    owners = select(OrganizationMember.user_id).where(
        OrganizationMember.organization_id == organization_id,
        OrganizationMember.role == OrganizationRole.OWNER,
        OrganizationMember.status == MembershipStatus.ACTIVE,
    )
    candidates = db.execute(
        select(OrganizationMember.organization_id)
        .where(
            OrganizationMember.user_id.in_(owners),
            OrganizationMember.role == OrganizationRole.OWNER,
            OrganizationMember.status == MembershipStatus.ACTIVE,
        )
        .distinct()
    ).scalars().all()
    pool = {organization_id}
    for candidate in candidates:
        if candidate in pool:
            continue
        other = resolve_tier(db, organization_id=candidate)
        if other is not None and other.key == FREE_TIER_KEY:
            pool.add(candidate)
    return tuple(sorted(pool, key=str))


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


def plan_limit(
    db: Session,
    *,
    organization_id: uuid.UUID,
    limit_key: str,
    at: Optional[datetime] = None,
) -> Optional[int]:
    """The organization's plan limit `limit_key` (`limit.*`), or None when its plan sets none.

    None also when the organization resolves no plan at all (an organization created
    before every organization started on Free; the plan seed repairs those).
    """
    if not plan_limits.is_plan_limit_key(limit_key):
        raise ValueError(f"{limit_key!r} is not a registered plan limit.")
    tier = resolve_tier(db, organization_id=organization_id, at=at)
    if tier is None:
        return None
    for entry in tier.entries:
        if entry.limit_key == limit_key and entry.max_quantity is not None:
            return int(entry.max_quantity)
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
    pool = usage_pool(db, organization_id=organization_id)

    keys = [TOTAL_COST_KEY] + [
        name for name in sorted(USAGE_EVENT_TYPES) if is_limit_key(name)
    ]

    statuses: list[QuotaStatus] = []
    for limit_key in keys:
        for limit in spend.effective_limits(
            db, organization_id=organization_id, limit_key=limit_key, lock=False
        ):
            since = spend.period_start(limit.period, now=moment)
            # Campaign session 1: a tier allowance is the usage pool's (one Free
            # allowance per owner account); an explicit limit is this organization's.
            members = (
                (organization_id,)
                if getattr(limit, "source", None) == "ORGANIZATION"
                else pool
            )
            current_qty, current_cost = spend.pooled_usage(
                db, pool=members, since=since, limit_key=limit_key, now=moment
            )

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
        # ARCH-30 Tranche 1 (T4-F1). Capability entitlements are validated by
        # their own rule, BEFORE the meter-vocabulary refusal below.
        #
        # Without this branch `llm.platform_key` reached the refusal, every
        # seeded tier was rejected, the seed's single transaction rolled all
        # four back, and `_platform_key_entitled` correctly refused inference
        # to every tenant without BYOK. The ordering is not an accident: an
        # entitlement must never fall through to `is_limit_key`, and
        # `entitlements._assert_disjoint_from_meters` guarantees no key can
        # satisfy both, so there is no string for which the order matters.
        if entitlements.is_entitlement_key(spec.limit_key):
            violation = entitlements.shape_violation(
                limit_key=spec.limit_key,
                period=spec.period,
                max_quantity=spec.max_quantity,
                max_cost_micros=spec.max_cost_micros,
                overage_policy=spec.overage_policy,
                overage_price_tier_key=spec.overage_price_tier_key,
                grace_quantity=spec.grace_quantity,
            )
            if violation is not None:
                raise QuotaTierValidationError(violation)
            entitlement_scope = (spec.limit_key, spec.period.value)
            if entitlement_scope in seen:
                raise QuotaTierValidationError(
                    f"Duplicate entry {entitlement_scope!r}."
                )
            seen.add(entitlement_scope)
            validated.append(spec)
            continue

        # Campaign session 1. Static plan limits (`limit.*`): a ceiling on a size, not
        # consumption, validated by their own shape rule like entitlements are.
        if plan_limits.is_plan_limit_key(spec.limit_key):
            violation = plan_limits.shape_violation(
                limit_key=spec.limit_key,
                period=spec.period,
                max_quantity=spec.max_quantity,
                max_cost_micros=spec.max_cost_micros,
                overage_policy=spec.overage_policy,
                overage_price_tier_key=spec.overage_price_tier_key,
                grace_quantity=spec.grace_quantity,
            )
            if violation is not None:
                raise QuotaTierValidationError(violation)
            limit_scope = (spec.limit_key, spec.period.value)
            if limit_scope in seen:
                raise QuotaTierValidationError(f"Duplicate entry {limit_scope!r}.")
            seen.add(limit_scope)
            validated.append(spec)
            continue

        if not is_limit_key(spec.limit_key):
            raise QuotaTierValidationError(
                f"'{spec.limit_key}' is neither the wildcard '{TOTAL_COST_KEY}', "
                f"a billable usage event type, nor a registered entitlement."
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
    commercials: Optional[TierCommercials] = None,
) -> QuotaTier:
    moment = _as_utc(effective_from)
    if commercials is not None:
        problem = commercials.violation()
        if problem is not None:
            raise QuotaTierValidationError(f"Tier {key} v{version}: {problem}")
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
        # Written at INSERT, while `published_at` is still NULL. The
        # immutability trigger refuses any later UPDATE of these columns.
        unit_amount_micros=(commercials.unit_amount_micros if commercials else None),
        currency=(commercials.currency if commercials else None),
        billing_interval=(commercials.billing_interval if commercials else None),
        gateway_price_id=(commercials.gateway_price_id if commercials else None),
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


def published_tier_by_key(
    db: Session,
    *,
    key: str,
    at: Optional[datetime] = None,
) -> Optional[_TierSnapshot]:
    """The tier version currently on sale under `key`, or None.

    ARCH-29 Tranche 2. `create_checkout_session` needs the price attached to
    the tier the caller named, and it must be the SAME version `list_plans`
    showed them — so this filters through `list_published_tiers` rather than
    querying `quota_tiers` directly. A second query with its own notion of
    "current" is how a customer comes to check out at a price they were never
    shown: the read path and the sell path would each be internally consistent
    and disagree with each other.
    """
    for tier in list_published_tiers(db, at=at):
        if tier.key == key:
            return tier
    return None


__all__ = [
    "FREE_TIER_KEY",
    "OverageOutcome",
    "plan_limit",
    "seat_factor",
    "usage_pool",
    "published_tier_by_key",
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