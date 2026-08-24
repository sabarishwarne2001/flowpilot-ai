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
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
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
from app.services.billing import account_service, stripe_gateway, subscription_service

logger = logging.getLogger("app.services.billing.seat")

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