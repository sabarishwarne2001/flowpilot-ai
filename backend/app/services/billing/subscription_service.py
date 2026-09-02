"""ARCH-15 Step 15.3 — the versioned subscription upsert (F2, F3).

Two things happen here and nothing else should:

1. **The guarded write.** Every write carries a monotonic
   `stripe_state_version` and is predicated on
   `WHERE stripe_state_version < :new`, so a stale fetch landing last is a
   no-op rather than a regression.
2. **The pin.** A subscription names one *version* of a quota tier and one
   *version* of a price book, chosen when the plan is chosen and not
   re-derived at read time.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.billing_account import BillingAccount
from app.models.organization import Organization
from app.models.price_book import PriceBook
from app.models.quota_tier import QuotaTier
from app.models.subscription import (
    LIVE_SUBSCRIPTION_STATUSES,
    Subscription,
    SubscriptionStatus,
)
from app.services.billing.stripe_gateway import StripeSubscriptionSnapshot

logger = logging.getLogger("app.services.billing.subscription")

#: The Stripe metadata key that names the plan. Written by the Checkout
#: session (15.7) and read back here, so the tier a customer bought is
#: recorded on the object they bought it with rather than inferred from a
#: price id we would then have to keep a second mapping for.
TIER_METADATA_KEY: str = "quota_tier_key"


class SubscriptionError(Exception):
    """Base class for subscription refusals."""


class UnmappableTierError(SubscriptionError):
    """The subscription names a tier we cannot resolve to a published version."""


class StaleStateError(SubscriptionError):
    """A write was refused because a newer state is already recorded."""


# ============================================================================
# Pin resolution (F3)
# ============================================================================


def resolve_tier_version(
    db: Session, *, tier_key: str, at: Optional[datetime] = None
) -> QuotaTier:
    """The published tier version in force for `tier_key` at `at`.

    Resolved **once**, at pin time. Every later read goes through the pinned
    id, so publishing `business/v4` does not retroactively change what a
    customer on `business/v3` was entitled to — which is the question "why was
    I refused on March 14?" still having a correct answer in July.
    """
    moment = at or datetime.now(timezone.utc)
    tier = db.execute(
        select(QuotaTier)
        .where(
            QuotaTier.key == tier_key,
            QuotaTier.is_active.is_(True),
            QuotaTier.published_at.is_not(None),
            QuotaTier.effective_from <= moment,
            (QuotaTier.effective_to.is_(None)) | (QuotaTier.effective_to > moment),
        )
        .order_by(QuotaTier.version.desc())
        .limit(1)
    ).scalar_one_or_none()

    if tier is None:
        raise UnmappableTierError(
            f"No published quota tier {tier_key!r} is in force at "
            f"{moment.isoformat()}. Refusing to pin a subscription to a tier "
            "that does not exist: guessing here entitles a paying customer to "
            "the wrong plan, silently, for a whole billing period."
        )
    return tier


def resolve_price_book(db: Session, *, at: Optional[datetime] = None) -> PriceBook:
    moment = at or datetime.now(timezone.utc)
    book = db.execute(
        select(PriceBook)
        .where(
            PriceBook.is_active.is_(True),
            PriceBook.published_at.is_not(None),
            PriceBook.effective_from <= moment,
            (PriceBook.effective_to.is_(None)) | (PriceBook.effective_to > moment),
        )
        .order_by(PriceBook.version.desc())
        .limit(1)
    ).scalar_one_or_none()

    if book is None:
        raise UnmappableTierError(
            f"No published price book is in force at {moment.isoformat()}. A "
            "subscription cannot be pinned to prices that do not exist."
        )
    return book


def tier_key_from(snapshot: StripeSubscriptionSnapshot) -> str:
    """Which plan this subscription is, according to Stripe.

    `BILLING_DEFAULT_QUOTA_TIER_KEY` being `None` means *refuse*, and that is
    the intended default. A subscription we cannot map to a tier is billing
    state we must not guess at; the row goes FAILED, an operator sees it, and
    the fix is one metadata edit in the Stripe dashboard. The alternative —
    defaulting to `free` — hands a paying customer the wrong entitlement and
    tells nobody.
    """
    key = (snapshot.metadata or {}).get(TIER_METADATA_KEY)
    if key:
        return str(key).strip()

    fallback = settings.BILLING_DEFAULT_QUOTA_TIER_KEY
    if fallback:
        logger.warning(
            "subscription.tier_key_defaulted",
            extra={
                "stripe_subscription_id": snapshot.id,
                "tier_key": fallback,
            },
        )
        return str(fallback).strip()

    raise UnmappableTierError(
        f"Stripe subscription {snapshot.id} carries no "
        f"`metadata.{TIER_METADATA_KEY}` and no "
        "BILLING_DEFAULT_QUOTA_TIER_KEY is configured. Set the metadata on "
        "the subscription rather than guessing the plan here."
    )


# ============================================================================
# Reads
# ============================================================================


def get_by_stripe_id(
    db: Session, *, stripe_subscription_id: str
) -> Optional[Subscription]:
    return db.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    ).scalar_one_or_none()


def live_subscription_for_organization(
    db: Session, *, organization_id: uuid.UUID
) -> Optional[Subscription]:
    return db.execute(
        select(Subscription)
        .join(BillingAccount, BillingAccount.id == Subscription.billing_account_id)
        .where(
            BillingAccount.organization_id == organization_id,
            Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES),
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def organization_id_for(db: Session, subscription: Subscription) -> uuid.UUID:
    return db.execute(
        select(BillingAccount.organization_id).where(
            BillingAccount.id == subscription.billing_account_id
        )
    ).scalar_one()


# ============================================================================
# The guarded upsert (F2)
# ============================================================================


def upsert_from_stripe(
    db: Session,
    *,
    account: BillingAccount,
    snapshot: StripeSubscriptionSnapshot,
) -> tuple[Optional[Subscription], bool]:
    """Write authoritative state, or decline because newer state exists.

    Returns `(subscription, applied)`. `applied is False` means the write was
    a no-op because the row already carries a `stripe_state_version` greater
    than or equal to this snapshot's — i.e. an older fetch landed after a
    newer one, which is the exact race F2 predicts and the only one
    re-fetching does not remove on its own.

    The guard is expressed as a `WHERE` on the `DO UPDATE`, not as a
    read-compare-write in Python. Two workers reconciling concurrently would
    both pass a Python comparison and both write; only the predicate inside
    the statement is actually atomic.
    """
    status = _coerce_status(snapshot.status)
    existing = get_by_stripe_id(db, stripe_subscription_id=snapshot.id)

    tier, book = _resolve_pins(db, snapshot=snapshot, existing=existing)

    values: dict[str, Any] = {
        "billing_account_id": account.id,
        "stripe_subscription_id": snapshot.id,
        "status": status.value,
        "quota_tier_key": tier.key,
        "quota_tier_id": tier.id,
        "price_book_id": book.id,
        "seats_purchased": int(snapshot.seats),
        "current_period_start": snapshot.current_period_start,
        "current_period_end": snapshot.current_period_end,
        "cancel_at_period_end": bool(snapshot.cancel_at_period_end),
        "cancel_at": snapshot.cancel_at,
        "canceled_at": snapshot.canceled_at,
        "trial_end": snapshot.trial_end,
        "stripe_state_version": int(snapshot.state_version),
        "last_reconciled_at": datetime.now(timezone.utc),
    }

    table = Subscription.__table__
    stmt = pg_insert(table).values(**values)
    stmt = (
        stmt.on_conflict_do_update(
            index_elements=[table.c.stripe_subscription_id],
            set_={
                key: getattr(stmt.excluded, key)
                for key in values
                if key != "stripe_subscription_id"
            },
            # F2's residual race, closed in SQL.
            where=table.c.stripe_state_version < stmt.excluded.stripe_state_version,
        )
        .returning(table.c.id)
    )

    written_id = db.execute(stmt).scalar_one_or_none()

    if written_id is None:
        logger.info(
            "subscription.stale_write_ignored",
            extra={
                "stripe_subscription_id": snapshot.id,
                "offered_version": snapshot.state_version,
                "recorded_version": (
                    existing.stripe_state_version if existing else None
                ),
            },
        )
        return existing, False

    db.expire_all()
    subscription = get_by_stripe_id(db, stripe_subscription_id=snapshot.id)

    # The other half of F3: ARCH-14's enforcement path reads the tier through
    # the organization pointer as well, so a plan change propagates to quota
    # by writing one row here rather than by anyone remembering to.
    if subscription is not None:
        _propagate_tier_to_organization(
            db, account=account, subscription=subscription
        )

    logger.info(
        "subscription.reconciled",
        extra={
            "stripe_subscription_id": snapshot.id,
            "status": status.value,
            "seats_purchased": snapshot.seats,
            "quota_tier_key": tier.key,
            "quota_tier_version": tier.version,
            "price_book_version": book.version,
            "state_version": snapshot.state_version,
        },
    )
    return subscription, True


def _resolve_pins(
    db: Session,
    *,
    snapshot: StripeSubscriptionSnapshot,
    existing: Optional[Subscription],
) -> tuple[QuotaTier, PriceBook]:
    """Choose the tier version and price book this row pins to.

    The rule, stated once so it is not rediscovered by argument later:

    * **New subscription** — pin to what is in force now.
    * **Same plan** — keep the existing pins. A `customer.subscription.updated`
      for a seat change must not silently re-pin a customer onto a price book
      published last Tuesday; that would make an invoice irreproducible, which
      is the whole thing A9 exists to prevent.
    * **Plan changed** — a different `metadata.quota_tier_key` is a new
      agreement, so re-pin both. The tier because it is the plan; the price
      book because agreeing a new plan is agreeing today's prices.
    """
    requested_key = tier_key_from(snapshot)
    period_start = snapshot.current_period_start

    if existing is None:
        return (
            resolve_tier_version(db, tier_key=requested_key, at=period_start),
            resolve_price_book(db, at=period_start),
        )

    if existing.quota_tier_key == requested_key:
        tier = db.execute(
            select(QuotaTier).where(QuotaTier.id == existing.quota_tier_id)
        ).scalar_one()
        book = db.execute(
            select(PriceBook).where(PriceBook.id == existing.price_book_id)
        ).scalar_one()
        return tier, book

    logger.info(
        "subscription.plan_changed",
        extra={
            "stripe_subscription_id": snapshot.id,
            "from_tier": existing.quota_tier_key,
            "to_tier": requested_key,
        },
    )
    changed_at = datetime.now(timezone.utc)
    return (
        resolve_tier_version(db, tier_key=requested_key, at=changed_at),
        resolve_price_book(db, at=changed_at),
    )


def _propagate_tier_to_organization(
    db: Session, *, account: BillingAccount, subscription: Subscription
) -> None:
    organization = db.execute(
        select(Organization).where(Organization.id == account.organization_id)
    ).scalar_one_or_none()
    if organization is None:
        return
    if organization.quota_tier_id == subscription.quota_tier_id:
        return
    organization.quota_tier_id = subscription.quota_tier_id
    db.flush()
    logger.info(
        "organization.quota_tier_pinned",
        extra={
            "organization_id": str(organization.id),
            "quota_tier_id": str(subscription.quota_tier_id),
            "quota_tier_key": subscription.quota_tier_key,
        },
    )


def _coerce_status(raw: str) -> SubscriptionStatus:
    try:
        return SubscriptionStatus(str(raw).strip().lower())
    except ValueError as exc:
        raise SubscriptionError(
            f"Stripe reported subscription status {raw!r}, which is not in "
            "the vocabulary this schema mirrors. Refusing rather than "
            "coercing: an unknown status is a Stripe API change and needs a "
            "migration, not a fallback."
        ) from exc


def record_seat_count(
    db: Session,
    *,
    subscription: Subscription,
    seats: int,
    state_version: int,
) -> bool:
    """Record a seat change we just made at Stripe, guarded the same way.

    Used immediately after `set_subscription_seats` so the local row reflects
    the change without waiting for the webhook. The webhook still arrives and
    still re-fetches; this write simply loses that race harmlessly, because
    the later fetch carries the larger version.
    """
    table = Subscription.__table__
    result = db.execute(
        table.update()
        .where(
            table.c.id == subscription.id,
            table.c.stripe_state_version < int(state_version),
        )
        .values(
            seats_purchased=int(seats),
            stripe_state_version=int(state_version),
            last_reconciled_at=func.now(),
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    applied = bool(result.rowcount)
    if applied:
        db.expire(subscription)
    return applied


__all__ = [
    "StaleStateError",
    "SubscriptionError",
    "TIER_METADATA_KEY",
    "UnmappableTierError",
    "get_by_stripe_id",
    "live_subscription_for_organization",
    "organization_id_for",
    "record_seat_count",
    "resolve_price_book",
    "resolve_tier_version",
    "tier_key_from",
    "upsert_from_stripe",
]
