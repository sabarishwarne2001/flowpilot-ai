"""ARCH-15 Step 15.4 — seats: derived, then asserted (F4).

    billable_seats  (a view over ACTIVE memberships)   = what is true
    subscriptions.seats_purchased                      = what Stripe believes

Those two disagreeing is **a symptom, not a bug**. The cause is always
upstream: a failed Stripe modify, a membership change that did not emit, an
out-of-order reconcile that lost a race. So `detect_drift` reports the delta
*and* the evidence, `sync_seats` fixes it and says loudly that it had to, and
neither of them pretends the fix was the point. A job that silently corrects
drift hides the thing that caused it, and the thing that caused it will happen
again next month on a bigger account.

PRORATION IS STRIPE'S JOB
========================

`set_subscription_seats` passes `proration_behavior` and computes nothing.
Deriving a prorated amount locally guarantees eventually disagreeing with the
invoice Stripe issues — over a leap day, a mid-period plan change, a trial
ending an hour into a period — and the customer is looking at Stripe's number,
not ours.

ARCH-24 did not relax that. `seat_price_disclosure` below has to put a real
proration figure in front of an admin before a JIT provision allocates a seat,
and it does it by *asking Stripe* through `preview_seat_change`, not by
multiplying a unit price by a fraction of a month. When Stripe cannot be
reached the function returns `proration_micros=None` and says why. An unknown
proration renders as unknown; it never renders as a number we made up, for the
same reason `COALESCE(cost_basis_micros, 0)` is banned two services over.

The seat *unit price* is a different question with a different answer: it comes
from the price book, resolved against the subscription's pinned book by
`invoice_service.seat_price_entry`, so the disclosed figure and the invoiced
figure are one lookup rather than two.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.billable_seat import BillableSeat
from app.models.billing_account import BillingAccount
from app.models.organization import (
    MembershipStatus,
    OrganizationMember,
)
from app.models.subscription import LIVE_SUBSCRIPTION_STATUSES, Subscription
from app.services import outbox_service
from app.services.billing import (
    account_service,
    invoice_service,
    stripe_gateway,
    subscription_service,
)

logger = logging.getLogger("app.services.billing.seat")

#: How long the disclosure endpoint will wait on Stripe before giving up and
#: reporting the proration as unknown. Deliberately short: a human is waiting
#: on a page, and a slow honest "unknown" beats a fast invented number.
SEAT_PREVIEW_TIMEOUT_SECONDS: float = 4.0

SEAT_ADDED_EVENT: str = "billing.seat_added"
SEAT_REMOVED_EVENT: str = "billing.seat_removed"
SEAT_SYNC_NEEDED_EVENT: str = "billing.seat_sync_needed"


class SeatError(Exception):
    """Base class for seat refusals."""


@dataclass(frozen=True)
class SeatDrift:
    organization_id: uuid.UUID
    subscription_id: uuid.UUID
    stripe_subscription_id: str
    seats_billable: int
    seats_purchased: int

    @property
    def delta(self) -> int:
        return self.seats_billable - self.seats_purchased

    @property
    def has_drift(self) -> bool:
        return self.delta != 0

    @property
    def direction(self) -> str:
        if self.delta > 0:
            return "UNDER_BILLED"
        if self.delta < 0:
            return "OVER_BILLED"
        return "IN_SYNC"

    def as_dict(self) -> dict[str, Any]:
        return {
            "organization_id": str(self.organization_id),
            "subscription_id": str(self.subscription_id),
            "stripe_subscription_id": self.stripe_subscription_id,
            "seats_billable": self.seats_billable,
            "seats_purchased": self.seats_purchased,
            "delta": self.delta,
            "direction": self.direction,
        }


# ============================================================================
# Derivation
# ============================================================================


def billable_seats(db: Session, *, organization_id: uuid.UUID) -> int:
    """Seats, read from the view.

    Through `BillableSeat` rather than a hand-written `count(*)` so there is
    exactly one definition of what a seat is. A second count written inline
    somewhere is a second definition, and the two will diverge on the day
    somebody adds a membership status.
    """
    seats = db.execute(
        select(BillableSeat.seats).where(
            BillableSeat.organization_id == organization_id
        )
    ).scalar_one_or_none()
    return int(seats or 0)


def billable_seats_direct(db: Session, *, organization_id: uuid.UUID) -> int:
    """The same count computed against the base table.

    Exists solely so Gate 15.4 can assert the view and the underlying
    predicate agree. Nothing in application code should call it — if the view
    and this disagree, the view is wrong and the fix is a migration.
    """
    return int(
        db.execute(
            select(func.count())
            .select_from(OrganizationMember)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.status == MembershipStatus.ACTIVE,
            )
        ).scalar_one()
    )


# ============================================================================
# Emission (the ARCH-13 substrate)
# ============================================================================


def _emit_seat_event(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
    resource_id: Optional[uuid.UUID] = None,
) -> None:
    """Emit, but only for a tenant that actually has billing.

    Skipping tenants with no billing account is not an optimisation. It is
    what stops the outbox filling with seat traffic for trial and internal
    organizations that will never be charged, and it keeps the ARCH-13
    internal consumer's backlog proportional to revenue rather than to signups.
    """
    if not account_service.has_billing_account(db, organization_id=organization_id):
        return

    outbox_service.emit_internal(
        db,
        organization_id=organization_id,
        event_type=event_type,
        payload=payload,
        resource_id=resource_id,
        require_active_transaction=False,
    )


def record_seat_added(
    db: Session,
    *,
    organization_id: uuid.UUID,
    membership_id: Optional[uuid.UUID] = None,
    user_id: Optional[uuid.UUID] = None,
    cause: str = "membership_activated",
) -> None:
    """A membership entered ACTIVE.

    Called from the transition itself, because the transition is the only
    moment at which anybody knows it happened. F4's first mismatch lives here:
    an ARCH-04 invitation is *not* a seat — there is no membership row to
    activate until it is accepted, and accepting is what calls this.
    """
    _emit_seat_event(
        db,
        organization_id=organization_id,
        event_type=SEAT_ADDED_EVENT,
        resource_id=membership_id,
        payload={
            "organization_id": str(organization_id),
            "membership_id": str(membership_id) if membership_id else None,
            "user_id": str(user_id) if user_id else None,
            "cause": cause,
            "seats_billable": billable_seats(db, organization_id=organization_id),
        },
    )


def record_seat_removed(
    db: Session,
    *,
    organization_id: uuid.UUID,
    membership_id: Optional[uuid.UUID] = None,
    user_id: Optional[uuid.UUID] = None,
    cause: str = "membership_deactivated",
) -> None:
    _emit_seat_event(
        db,
        organization_id=organization_id,
        event_type=SEAT_REMOVED_EVENT,
        resource_id=membership_id,
        payload={
            "organization_id": str(organization_id),
            "membership_id": str(membership_id) if membership_id else None,
            "user_id": str(user_id) if user_id else None,
            "cause": cause,
            "seats_billable": billable_seats(db, organization_id=organization_id),
        },
    )


def request_seat_sync(
    db: Session,
    *,
    organization_id: uuid.UUID,
    reason: str,
    drift: Optional[SeatDrift] = None,
) -> None:
    """Ask for Stripe to be re-asserted. Not an instruction to overwrite."""
    payload: dict[str, Any] = {
        "organization_id": str(organization_id),
        "reason": reason,
    }
    if drift is not None:
        payload["drift"] = drift.as_dict()
    _emit_seat_event(
        db,
        organization_id=organization_id,
        event_type=SEAT_SYNC_NEEDED_EVENT,
        payload=payload,
    )


def on_ownership_transferred(
    db: Session, *, organization_id: uuid.UUID
) -> None:
    """F4's second mismatch, stated as code.

    An ownership transfer changes who administers a tenant. It changes **no
    seat count** — the same people are still members — and it does **not**
    move the billing address, because the Stripe customer follows the
    organization and not the owner. Moving it implicitly would put a payment
    instrument in the hands of somebody who agreed to become an owner, not a
    payer.

    All this does is ask for a re-assert, so the transfer shows up in the
    reconcile trail rather than looking like a silent gap.
    """
    request_seat_sync(
        db, organization_id=organization_id, reason="ownership_transferred"
    )


# ============================================================================
# Drift
# ============================================================================


@dataclass(frozen=True)
class SeatPriceDisclosure:
    """Everything the JIT policy panel is allowed to render, and its provenance.

    Two independently-unknowable figures live here, and they fail separately:

      * `unit_price_micros` is None when the pinned price book has no seat
        entry. That is a configuration fault and the panel should say so.
      * `proration_micros` is None when Stripe could not be reached in time.
        That is a transient fault and the panel should say *that*.

    Collapsing either to 0 would put a free seat in front of an administrator
    about to provision a paid one.
    """

    organization_id: uuid.UUID
    seats_current: int
    seats_after: int

    unit_price_micros: Optional[int]
    unit: Optional[str]
    currency: str
    price_book_id: Optional[uuid.UUID]
    price_book_version: Optional[int]
    price_source: str

    proration_micros: Optional[int]
    proration_source: str
    proration_unavailable_reason: Optional[str]

    period_start: Optional[datetime]
    period_end: Optional[datetime]

    @property
    def is_priced(self) -> bool:
        return self.unit_price_micros is not None

    @property
    def proration_is_known(self) -> bool:
        return self.proration_micros is not None


#: Provenance discriminators. `24-G6` asserts the disclosed proration is
#: sourced, so the value that means "we computed it ourselves" deliberately
#: does not exist in this vocabulary.
PRICE_SOURCE_BOOK: str = "PRICE_BOOK"
PRICE_SOURCE_UNPRICED: str = "UNPRICED"
PRORATION_SOURCE_STRIPE: str = "STRIPE_PREVIEW"
PRORATION_SOURCE_UNAVAILABLE: str = "UNAVAILABLE"


def seat_price_disclosure(
    db: Session,
    *,
    organization_id: uuid.UUID,
    additional_seats: int = 1,
    gateway: Optional[Any] = None,
) -> SeatPriceDisclosure:
    """Seat unit price from the price book, proration from Stripe.

    Never raises for a Stripe failure. A disclosure with an unknown proration
    is still useful — the unit price alone answers most of the question — and
    an exception here would take out the whole IdP policy panel over a
    third-party timeout.
    """
    if additional_seats < 1:
        raise SeatError("additional_seats must be at least 1.")

    subscription = subscription_service.live_subscription_for_organization(
        db, organization_id=organization_id
    )

    if subscription is None:
        seats_now = billable_seats(db, organization_id=organization_id)
        return SeatPriceDisclosure(
            organization_id=organization_id,
            seats_current=seats_now,
            seats_after=seats_now + additional_seats,
            unit_price_micros=None,
            unit=None,
            currency="USD",
            price_book_id=None,
            price_book_version=None,
            price_source=PRICE_SOURCE_UNPRICED,
            proration_micros=None,
            proration_source=PRORATION_SOURCE_UNAVAILABLE,
            proration_unavailable_reason=(
                "This organization has no live subscription, so a seat change "
                "has no price to disclose."
            ),
            period_start=None,
            period_end=None,
        )

    current = int(subscription.seats_purchased)
    after = current + additional_seats

    entry = invoice_service.seat_price_entry(
        db, price_book_id=subscription.price_book_id
    )

    if entry is None:
        unit_price: Optional[int] = None
        unit: Optional[str] = None
        price_source = PRICE_SOURCE_UNPRICED
        logger.error(
            "billing.seat_disclosure_unpriced",
            extra={
                "organization_id": str(organization_id),
                "price_book_id": str(subscription.price_book_id),
                "event_type": settings.BILLING_SEAT_EVENT_TYPE,
            },
        )
    else:
        # Quantised to whole micros here rather than in the schema: the panel
        # renders what it is given, and a Decimal that formats differently in
        # two places is the drift ARCH-21 formatMeasurement exists to prevent.
        unit_price = int(
            Decimal(str(entry.unit_price_micros)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        unit = entry.unit
        price_source = PRICE_SOURCE_BOOK

    proration: Optional[int] = None
    proration_source = PRORATION_SOURCE_UNAVAILABLE
    reason: Optional[str] = None
    period_start: Optional[datetime] = subscription.current_period_start
    period_end: Optional[datetime] = subscription.current_period_end

    try:
        client = gateway or stripe_gateway.get_gateway()
        preview = client.preview_seat_change(
            subscription_id=subscription.stripe_subscription_id,
            seats=after,
            timeout_seconds=SEAT_PREVIEW_TIMEOUT_SECONDS,
        )
        proration = int(preview.proration_micros)
        proration_source = PRORATION_SOURCE_STRIPE
        if preview.period_start is not None:
            period_start = preview.period_start
        if preview.period_end is not None:
            period_end = preview.period_end
    except Exception as exc:  # noqa: BLE001 — see docstring
        reason = (
            "Stripe could not be reached for a proration preview. The figure "
            "is unknown rather than zero; it will be whatever Stripe invoices."
        )
        logger.warning(
            "billing.seat_preview_unavailable",
            extra={
                "organization_id": str(organization_id),
                "subscription_id": str(subscription.id),
                "error": type(exc).__name__,
            },
        )

    return SeatPriceDisclosure(
        organization_id=organization_id,
        seats_current=current,
        seats_after=after,
        unit_price_micros=unit_price,
        unit=unit,
        currency=(entry_currency(db, subscription) if entry is not None else "USD"),
        price_book_id=subscription.price_book_id if entry is not None else None,
        price_book_version=(
            subscription.price_book.version
            if entry is not None and subscription.price_book is not None
            else None
        ),
        price_source=price_source,
        proration_micros=proration,
        proration_source=proration_source,
        proration_unavailable_reason=reason,
        period_start=period_start,
        period_end=period_end,
    )


def entry_currency(db: Session, subscription: Subscription) -> str:
    """Currency of the pinned book, defaulting to USD when unavailable."""
    book = subscription.price_book
    return str(getattr(book, "currency", None) or "USD").upper()


def detect_drift(
    db: Session, *, organization_id: uuid.UUID
) -> Optional[SeatDrift]:
    """Compare belief with truth for one organization.

    Returns `None` when there is no live subscription — a tenant with no
    subscription cannot drift, it simply is not billed.
    """
    subscription = subscription_service.live_subscription_for_organization(
        db, organization_id=organization_id
    )
    if subscription is None:
        return None

    return SeatDrift(
        organization_id=organization_id,
        subscription_id=subscription.id,
        stripe_subscription_id=subscription.stripe_subscription_id,
        seats_billable=billable_seats(db, organization_id=organization_id),
        seats_purchased=int(subscription.seats_purchased),
    )


def detect_all_drift(db: Session, *, limit: int = 1000) -> list[SeatDrift]:
    """Every live subscription whose seat count disagrees with the view.

    One query, left-joined against the view, because the alternative — a
    per-organization loop — turns a gate into an N+1 nobody runs often enough
    to be a gate.
    """
    rows = db.execute(
        select(
            BillingAccount.organization_id,
            Subscription.id,
            Subscription.stripe_subscription_id,
            Subscription.seats_purchased,
            func.coalesce(BillableSeat.seats, 0),
        )
        .join(BillingAccount, BillingAccount.id == Subscription.billing_account_id)
        .outerjoin(
            BillableSeat,
            BillableSeat.organization_id == BillingAccount.organization_id,
        )
        .where(Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES))
        .limit(limit)
    ).all()

    drifts = [
        SeatDrift(
            organization_id=organization_id,
            subscription_id=subscription_id,
            stripe_subscription_id=stripe_subscription_id,
            seats_billable=int(seats_billable),
            seats_purchased=int(seats_purchased),
        )
        for (
            organization_id,
            subscription_id,
            stripe_subscription_id,
            seats_purchased,
            seats_billable,
        ) in rows
    ]
    return [drift for drift in drifts if drift.has_drift]


def report_drift(db: Session, *, limit: int = 1000) -> list[SeatDrift]:
    """Detect, log at a level that gets noticed, and request a re-assert."""
    drifts = detect_all_drift(db, limit=limit)
    for drift in drifts:
        logger.error("billing.seat_drift", extra=drift.as_dict())
        request_seat_sync(
            db,
            organization_id=drift.organization_id,
            reason="drift_detected",
            drift=drift,
        )
    return drifts


# ============================================================================
# Sync
# ============================================================================


def sync_seats(
    db: Session,
    *,
    organization_id: uuid.UUID,
    reason: str = "seat_sync",
    force: bool = False,
) -> dict[str, Any]:
    """Make Stripe's seat count match the view, with proration.

    Returns a small outcome dict rather than raising on a no-op, because the
    common case — the counts already agree — is not exceptional and the job
    handler needs to record that it checked.
    """
    subscription = subscription_service.live_subscription_for_organization(
        db, organization_id=organization_id
    )
    if subscription is None:
        return {
            "organization_id": str(organization_id),
            "outcome": "NO_LIVE_SUBSCRIPTION",
        }

    seats = billable_seats(db, organization_id=organization_id)
    purchased = int(subscription.seats_purchased)

    if seats == purchased and not force:
        return {
            "organization_id": str(organization_id),
            "subscription_id": str(subscription.id),
            "outcome": "IN_SYNC",
            "seats": seats,
        }

    if not settings.BILLING_SEAT_SYNC_ENABLED:
        logger.warning(
            "billing.seat_sync_disabled",
            extra={
                "organization_id": str(organization_id),
                "seats_billable": seats,
                "seats_purchased": purchased,
            },
        )
        return {
            "organization_id": str(organization_id),
            "subscription_id": str(subscription.id),
            "outcome": "DISABLED",
            "seats_billable": seats,
            "seats_purchased": purchased,
        }

    # Logged before the call, at error level, with the delta and the
    # direction. Fixing drift without recording that there *was* drift is how
    # a recurring upstream fault stays invisible for two quarters.
    if seats != purchased:
        logger.error(
            "billing.seat_drift_correcting",
            extra={
                "organization_id": str(organization_id),
                "subscription_id": str(subscription.id),
                "seats_billable": seats,
                "seats_purchased": purchased,
                "delta": seats - purchased,
                "reason": reason,
            },
        )

    snapshot = stripe_gateway.get_gateway().set_subscription_seats(
        subscription_id=subscription.stripe_subscription_id,
        seats=seats,
        reason=reason,
    )

    applied = subscription_service.record_seat_count(
        db,
        subscription=subscription,
        seats=snapshot.seats,
        state_version=snapshot.state_version,
    )

    return {
        "organization_id": str(organization_id),
        "subscription_id": str(subscription.id),
        "outcome": "SYNCED" if applied else "SUPERSEDED",
        "seats_billable": seats,
        "seats_previously_purchased": purchased,
        "seats_now_purchased": snapshot.seats,
        "proration_behavior": settings.BILLING_SEAT_PRORATION_BEHAVIOR,
    }


def organizations_needing_sync(
    db: Session, *, limit: int = 1000
) -> Iterable[uuid.UUID]:
    return [drift.organization_id for drift in detect_all_drift(db, limit=limit)]


__all__ = [
    "PRICE_SOURCE_BOOK",
    "PRICE_SOURCE_UNPRICED",
    "PRORATION_SOURCE_STRIPE",
    "PRORATION_SOURCE_UNAVAILABLE",
    "SEAT_PREVIEW_TIMEOUT_SECONDS",
    "SeatPriceDisclosure",
    "seat_price_disclosure",
    "SEAT_ADDED_EVENT",
    "SEAT_REMOVED_EVENT",
    "SEAT_SYNC_NEEDED_EVENT",
    "SeatDrift",
    "SeatError",
    "billable_seats",
    "billable_seats_direct",
    "detect_all_drift",
    "detect_drift",
    "on_ownership_transferred",
    "organizations_needing_sync",
    "record_seat_added",
    "record_seat_removed",
    "report_drift",
    "request_seat_sync",
    "sync_seats",
]