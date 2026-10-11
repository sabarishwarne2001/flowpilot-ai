"""Campaign session 1 — one seat check for every way a person enters an organization.

THE MODEL
=========
The organization is the customer. Every member occupies one seat; members never pay.

  * **Free** sells no seats. Its seats come from the plan: the `limit.seats` row of the
    published Free tier (owner + one).
  * **A paid plan** holds a purchased seat quantity: `subscriptions.seats_purchased` on
    the live subscription, chosen at checkout and changed in Billing -> Seats (the
    gateway prorates). Adding someone past it needs a seat bought first, by someone
    allowed to buy seats, after the price was shown.
  * `organizations.seat_limit`, when set, is an additional hard cap (a platform
    operator's override); nothing in the product writes it.

"Used" is active members plus pending invitations: an invitation holds the seat its
acceptance will take, so seats cannot be walked past by inviting instead of adding.

Every seat-consuming path (invitation issue, resend and accept, direct add,
reactivation, SCIM, SSO just-in-time provisioning) calls `assert_seat_available`, which
takes a per-organization advisory lock first: two acceptances racing for the last seat
are serialised, and the second sees the first.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.exceptions import SeatLimitExceededError
from app.core.plan_limits import SEATS_LIMIT
from app.crud import organization_invitation as invitation_crud
from app.crud import organization_members as organization_members_crud
from app.models.organization import Organization

logger = logging.getLogger("app.services.seat_capacity")

SOURCE_PURCHASED = "PURCHASED"
SOURCE_PLAN = "PLAN"
SOURCE_OVERRIDE = "OVERRIDE"
SOURCE_UNLIMITED = "UNLIMITED"

#: The machine-readable reason a refusal carries in `details.reason`.
REASON_SEAT_LIMIT = "SEAT_LIMIT_REACHED"


@dataclass(frozen=True)
class SeatCapacity:
    organization_id: uuid.UUID
    plan_key: Optional[str]
    #: None means no limit at all (an organization with no plan and no override).
    capacity: Optional[int]
    source: str
    members: int
    pending_invitations: int
    #: True when seats can be bought on the current plan (a live paid subscription).
    can_purchase: bool

    @property
    def used(self) -> int:
        return self.members + self.pending_invitations

    @property
    def available(self) -> Optional[int]:
        if self.capacity is None:
            return None
        return max(0, self.capacity - self.used)

    def as_details(self) -> dict[str, Any]:
        return {
            "reason": REASON_SEAT_LIMIT,
            "plan": self.plan_key,
            "seat_capacity": self.capacity,
            "seats_used": self.used,
            "members": self.members,
            "pending_invitations": self.pending_invitations,
            "source": self.source,
            "can_purchase_seats": self.can_purchase,
            # What to do next: buy a seat on a paid plan, change plan on Free.
            "remedy": "BUY_SEATS" if self.can_purchase else "UPGRADE_PLAN",
        }


def lock_seats(db: Session, *, organization_id: uuid.UUID) -> None:
    """Serialise every seat-consuming change in one organization until commit."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"organization-seats:{organization_id}"},
    )


def seat_capacity(db: Session, *, organization_id: uuid.UUID) -> SeatCapacity:
    from app.services import quota_service
    from app.services.billing import subscription_service

    members = organization_members_crud.count_consumed_seats(db, organization_id=organization_id)
    pending = invitation_crud.count_pending_invitations(db, organization_id=organization_id)

    subscription = subscription_service.live_subscription_for_organization(
        db, organization_id=organization_id
    )
    tier = quota_service.resolve_tier(db, organization_id=organization_id)
    plan_key = tier.key if tier is not None else None

    capacity: Optional[int] = None
    source = SOURCE_UNLIMITED
    can_purchase = False
    if subscription is not None and (subscription.seats_purchased or 0) > 0:
        capacity = int(subscription.seats_purchased)
        source = SOURCE_PURCHASED
        can_purchase = True
        plan_key = subscription.quota_tier_key or plan_key
    else:
        plan_seats = quota_service.plan_limit(db, organization_id=organization_id, limit_key=SEATS_LIMIT)
        if plan_seats is not None:
            capacity = plan_seats
            source = SOURCE_PLAN

    override = db.execute(
        select(Organization.seat_limit).where(Organization.id == organization_id)
    ).scalar_one_or_none()
    if override is not None and (capacity is None or override < capacity):
        capacity = int(override)
        source = SOURCE_OVERRIDE

    return SeatCapacity(
        organization_id=organization_id,
        plan_key=plan_key,
        capacity=capacity,
        source=source,
        members=int(members),
        pending_invitations=int(pending),
        can_purchase=can_purchase,
    )


def assert_seat_available(
    db: Session,
    *,
    organization_id: uuid.UUID,
    message: Optional[str] = None,
    already_reserved: bool = False,
    adding: int = 1,
) -> SeatCapacity:
    """Refuse when the change needs a seat the organization does not have.

    `already_reserved` is for an operation on a pending invitation, whose seat the
    count already includes (F-221): accepting turns it into a member's seat and
    resending reserves nothing new, so those are refused only when the count is
    already past the capacity.
    """
    lock_seats(db, organization_id=organization_id)
    capacity = seat_capacity(db, organization_id=organization_id)
    if capacity.capacity is None:
        return capacity
    needed = capacity.used if already_reserved else capacity.used + adding
    if needed > capacity.capacity:
        logger.info(
            "seats.refused",
            extra={"organization_id": str(organization_id), **capacity.as_details()},
        )
        name = db.execute(
            select(Organization.name).where(Organization.id == organization_id)
        ).scalar_one_or_none()
        raise SeatLimitExceededError(
            message or default_message(capacity, name),
            details=capacity.as_details(),
        )
    return capacity


def default_message(capacity: SeatCapacity, organization_name: Optional[str] = None) -> str:
    """Why there is no seat, and what to do about it, in the inviter's words."""
    who = organization_name or "This organization"
    seats = f"{capacity.capacity} seat{'s' if capacity.capacity != 1 else ''}"
    if capacity.source == SOURCE_PURCHASED:
        return (
            f"{who} has no seats available: all {seats} on the subscription are taken by members "
            "and pending invitations. Add a seat in Billing -> Seats (owners, admins and billing "
            "managers can), then invite again."
        )
    if capacity.source == SOURCE_PLAN:
        plan = (capacity.plan_key or "current").capitalize()
        return (
            f"{who} has no seats available: the {plan} plan includes {seats} and members and "
            "pending invitations take all of them. Upgrade the plan to add more people."
        )
    return (
        f"{who} has no seats available: its limit of {seats} is taken by members and pending "
        "invitations."
    )


def active_member_count(db: Session, *, organization_id: uuid.UUID) -> int:
    from app.models.organization import MembershipStatus, OrganizationMember

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


__all__ = [
    "REASON_SEAT_LIMIT",
    "SeatCapacity",
    "active_member_count",
    "assert_seat_available",
    "default_message",
    "lock_seats",
    "seat_capacity",
]
