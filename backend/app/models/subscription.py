"""ARCH-15 Step 15.3 — `subscriptions` (F2, F3).

WHAT THIS ROW IS
================

A **cache of Stripe's state, reconciled by re-fetch**, plus three columns that
are ours and are *not* a cache: `quota_tier_id`, `price_book_id`, and
`stripe_state_version`.

F3 — WHY THE TIER AND THE BOOK ARE PINNED HERE
==============================================

ARCH-14 resolves a tenant's tier as `organizations.quota_tier_id -> key`, then
picks the version whose effective window covers *now*. That is correct for a
tenant with no subscription and wrong for one with a subscription, because it
means publishing `business/v4` retroactively changes what a customer on
`business/v3` was entitled to — including the allowance an already-issued
invoice was computed against.

So the subscription pins the tier **by id, at a version**, and ARCH-14's
enforcement path reads it from here (`quota_service.resolve_tier`). A plan
change is then one row write, and a price-book publication is not a plan change
for anybody.

`price_book_id` is pinned for the same reason and consumed by 15.6: an overage
line on a February invoice is priced from the book in force *for that
subscription in that period*, never from the active one.

F2 — WHY `stripe_state_version` EXISTS
======================================

Stripe does not guarantee delivery order. `customer.subscription.updated` can
arrive before the `created` it followed. Buffering and sorting by
`event.created` does not fix it: `created` has second granularity, so two
events in the same second stay ambiguous, and a buffer needs a flush deadline
that is itself a guess.

The design decision is to **reconcile, not apply**: ignore the event body's
`object` entirely and re-fetch the subscription from the API. A stale event
then triggers a fetch that returns current truth, and out-of-order delivery
becomes harmless rather than corrupting.

One race survives that: two events processed concurrently, where the *older*
fetch lands last. `stripe_state_version` is a monotonic marker taken when the
fetch is **issued** (not when it returns), and every write is
`... WHERE stripe_state_version < :new`, so a stale write is a no-op instead of
a regression. See `reconcile_service` for why the issue time and not the return
time is the correct marker.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.billing_account import BillingAccount
    from app.models.price_book import PriceBook
    from app.models.quota_tier import QuotaTier

SUBSCRIPTION_STATUS_ENUM_NAME: str = "subscription_status"


class SubscriptionStatus(str, PyEnum):
    """Mirrors Stripe's vocabulary exactly, lower case included.

    Mirroring rather than translating means a support conversation, a Stripe
    dashboard screenshot and a row in this table all use one word for one
    thing. The cost is that the enum *names* and *values* differ in case, which
    matters: SQLAlchemy persists a PEP-435 enum by **name** unless told
    otherwise, so every mapping of this type must pass `values_callable`. The
    module-level `SUBSCRIPTION_STATUS_VALUES` and the migration are the two
    other places the same list appears; all three are asserted equal by
    Gate 15.3.
    """

    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    PAUSED = "paused"


SUBSCRIPTION_STATUS_VALUES: tuple[str, ...] = tuple(
    member.value for member in SubscriptionStatus
)

#: The statuses that occupy the one-live-subscription-per-account slot.
#: `incomplete` is deliberately excluded: a checkout that was never completed
#: must not block a second, successful attempt. `paused` is excluded because a
#: paused subscription is not billing and a tenant may start a new plan.
LIVE_SUBSCRIPTION_STATUSES: tuple[SubscriptionStatus, ...] = (
    SubscriptionStatus.TRIALING,
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE,
    SubscriptionStatus.UNPAID,
)

LIVE_SUBSCRIPTION_VALUES: tuple[str, ...] = tuple(
    status.value for status in LIVE_SUBSCRIPTION_STATUSES
)

#: Statuses in which the tenant is entitled to the tier's allowance. `past_due`
#: is included: dunning degrades access on a schedule (15.8), not the instant a
#: card fails.
ENTITLED_SUBSCRIPTION_STATUSES: tuple[SubscriptionStatus, ...] = (
    SubscriptionStatus.TRIALING,
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE,
)


def subscription_status_enum() -> PGEnum:
    """The mapped enum type, with `values_callable` supplied.

    A helper rather than a module constant because a single `PGEnum` instance
    bound into two `mapped_column` calls shares state across metadata objects
    in a way that bites during test collection.
    """
    return PGEnum(
        SubscriptionStatus,
        name=SUBSCRIPTION_STATUS_ENUM_NAME,
        create_type=False,
        validate_strings=True,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


class Subscription(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "subscriptions"

    __table_args__ = (
        CheckConstraint(
            "current_period_end > current_period_start",
            name="ck_subscriptions_period_ordered",
        ),
        CheckConstraint(
            "seats_purchased >= 0", name="ck_subscriptions_seats_positive"
        ),
        CheckConstraint(
            "stripe_state_version >= 0",
            name="ck_subscriptions_state_version_non_negative",
        ),
        CheckConstraint(
            "length(stripe_subscription_id) > 0",
            name="ck_subscriptions_stripe_id_not_blank",
        ),
        CheckConstraint(
            "length(quota_tier_key) > 0", name="ck_subscriptions_tier_key_not_blank"
        ),
        # The ARCH-15 plan sketched this as a biconditional:
        #     (status = 'canceled') = (canceled_at IS NOT NULL)
        # That is wrong against real Stripe behaviour and would refuse a
        # perfectly ordinary object. When a customer cancels with
        # `cancel_at_period_end`, Stripe stamps `canceled_at` with the time of
        # the *request* while `status` stays `active` until the period
        # actually ends. The biconditional would reject the reconcile of every
        # scheduled cancellation, leave the row FAILED, and dead-letter it.
        #
        # Only one direction is invariant, so only one direction is asserted.
        CheckConstraint(
            f"status <> 'canceled'::{SUBSCRIPTION_STATUS_ENUM_NAME} "
            "OR canceled_at IS NOT NULL",
            name="ck_subscriptions_canceled_implies_canceled_at",
        ),
        CheckConstraint(
            "cancel_at_period_end IS FALSE OR canceled_at IS NOT NULL "
            "OR cancel_at IS NOT NULL",
            name="ck_subscriptions_scheduled_cancel_has_a_date",
        ),
        Index(
            "uq_subscriptions_stripe_subscription_id",
            "stripe_subscription_id",
            unique=True,
        ),
        # One live subscription per account. Partial, so historical canceled
        # rows stay queryable forever — 15.6 reproduces invoices against them.
        Index(
            "uq_subscriptions_live_account",
            "billing_account_id",
            unique=True,
            postgresql_where=text(
                "status IN ("
                + ", ".join(
                    f"'{value}'::{SUBSCRIPTION_STATUS_ENUM_NAME}"
                    for value in LIVE_SUBSCRIPTION_VALUES
                )
                + ")"
            ),
        ),
        Index(
            "ix_subscriptions_account_created",
            "billing_account_id",
            text("created_at DESC"),
        ),
        Index("ix_subscriptions_quota_tier_id", "quota_tier_id"),
        Index("ix_subscriptions_price_book_id", "price_book_id"),
        Index(
            "ix_subscriptions_period_end",
            "current_period_end",
            postgresql_where=text(
                "status IN ("
                + ", ".join(
                    f"'{value}'::{SUBSCRIPTION_STATUS_ENUM_NAME}"
                    for value in LIVE_SUBSCRIPTION_VALUES
                )
                + ")"
            ),
        ),
        Index(
            "ix_subscriptions_stale_reconcile",
            "last_reconciled_at",
        ),
    )

    billing_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )

    stripe_subscription_id: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[SubscriptionStatus] = mapped_column(
        subscription_status_enum(), nullable=False
    )

    # ---- F3: pinned, not looked up --------------------------------------
    quota_tier_key: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc=(
            "Denormalised from the pinned tier so a support query does not "
            "need a join, and so the key survives if the tier row is ever "
            "renamed. `quota_tier_id` is the authority."
        ),
    )
    quota_tier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quota_tiers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    price_book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("price_books.id", ondelete="RESTRICT"),
        nullable=False,
    )

    seats_purchased: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        doc=(
            "What Stripe believes. The `billable_seats` view is what is true. "
            "The two disagreeing is a symptom with an upstream cause — see "
            "seat_service.detect_drift."
        ),
    )

    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    cancel_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canceled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ---- F2: the monotonic guard ----------------------------------------
    stripe_state_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
        doc=(
            "Microseconds since the epoch at the moment the authoritative "
            "fetch was *issued*. Every write is guarded by "
            "`WHERE stripe_state_version < :new`."
        ),
    )
    last_reconciled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    billing_account: Mapped["BillingAccount"] = relationship(
        "BillingAccount", back_populates="subscriptions", lazy="joined"
    )
    quota_tier: Mapped["QuotaTier"] = relationship("QuotaTier", lazy="select")
    price_book: Mapped["PriceBook"] = relationship("PriceBook", lazy="select")

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_SUBSCRIPTION_STATUSES

    @property
    def is_entitled(self) -> bool:
        return self.status in ENTITLED_SUBSCRIPTION_STATUSES

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Subscription {self.stripe_subscription_id} "
            f"{self.status.value if self.status else None} "
            f"tier={self.quota_tier_key} seats={self.seats_purchased} "
            f"v={self.stripe_state_version}>"
        )


__all__ = [
    "ENTITLED_SUBSCRIPTION_STATUSES",
    "LIVE_SUBSCRIPTION_STATUSES",
    "LIVE_SUBSCRIPTION_VALUES",
    "SUBSCRIPTION_STATUS_ENUM_NAME",
    "SUBSCRIPTION_STATUS_VALUES",
    "Subscription",
    "SubscriptionStatus",
    "subscription_status_enum",
]