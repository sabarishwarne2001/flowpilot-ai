"""Seats on a paid plan are bought and released on purpose (campaign session 1).

    pytest tests/services/test_seat_purchase.py -q

A paid organization holds a purchased seat quantity (the capacity the seat check
enforces). There was no way to buy one: the only seat operation was "reconcile", which
set the gateway to the number of active members. Now `set_purchased_seats` buys or
releases seats at the gateway (which prorates), refuses a release below members plus
pending invitations, refuses a purchase at a price other than the one shown, and the
drift sweep treats unused seats as normal and members past the purchased seats as the
billing fault it is, queuing a real sync job instead of an event nothing consumes.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.job import Job
from app.models.organization import MembershipStatus
from app.services.billing import seat_service
from tests.services.test_arch15_gate_15_1_15_2_inbound import (  # noqa: F401
    FakeStripeGateway,
    billing_org,
    gateway,
    make_subscription,
    stripe_settings,
)
from tests.services.test_arch15_gate_15_3_15_4_subscriptions_seats import (
    add_member,
    add_pending_invitation,
    install_subscription,
)

#: The fixture price book's seat price.
SEAT_PRICE_MICROS = 25_000_000


@pytest.fixture(autouse=True)
def _job_handlers() -> None:
    """The worker's handlers, so a queued `billing.seat_sync` job is a real one."""
    from app.workers.handlers import register_all

    register_all()


def test_seats_are_bought_at_the_gateway_with_proration(db, gateway, billing_org) -> None:
    organization = billing_org["organization"]
    install_subscription(db, billing_org, gateway, seats=1)

    result = seat_service.set_purchased_seats(
        db, organization_id=organization.id, seats=4, confirmed_unit_price_micros=SEAT_PRICE_MICROS
    )

    assert result["outcome"] == "CHANGED"
    assert result["seats_now_purchased"] == 4
    assert gateway.seat_calls and gateway.seat_calls[-1][:2] == ("sub_gate15", 4)


def test_a_purchase_at_a_price_other_than_the_one_shown_is_refused(db, gateway, billing_org) -> None:
    install_subscription(db, billing_org, gateway, seats=1)

    with pytest.raises(seat_service.SeatPriceChangedError):
        seat_service.set_purchased_seats(
            db, organization_id=billing_org["organization"].id, seats=2, confirmed_unit_price_micros=1
        )
    assert gateway.seat_calls == []


def test_unused_seats_can_be_released_but_not_below_people_holding_them(db, gateway, billing_org) -> None:
    organization = billing_org["organization"]
    install_subscription(db, billing_org, gateway, seats=5)
    add_member(db, organization)  # owner + 1 member
    add_pending_invitation(db, organization, billing_org["owner"])  # + 1 invitation = 3 used

    with pytest.raises(seat_service.SeatsInUseError) as refused:
        seat_service.set_purchased_seats(db, organization_id=organization.id, seats=2)
    assert refused.value.seats_used == 3

    result = seat_service.set_purchased_seats(db, organization_id=organization.id, seats=3)
    assert result["seats_now_purchased"] == 3


def test_seats_are_bought_only_on_a_paid_subscription(db, gateway, billing_org) -> None:
    with pytest.raises(seat_service.NoPaidSubscriptionError):
        seat_service.set_purchased_seats(db, organization_id=billing_org["organization"].id, seats=3)


def test_reconcile_never_strands_a_pending_invitation(db, gateway, billing_org) -> None:
    """Releasing unused seats keeps the seat a pending invitation holds."""
    organization = billing_org["organization"]
    install_subscription(db, billing_org, gateway, seats=5)
    add_pending_invitation(db, organization, billing_org["owner"])

    result = seat_service.sync_seats(db, organization_id=organization.id)

    assert result["seats_now_purchased"] == 2, "owner + the invited person"


def test_unused_seats_are_not_reported_as_drift(db, gateway, billing_org) -> None:
    install_subscription(db, billing_org, gateway, seats=3)  # one member, two unused seats

    assert seat_service.report_drift(db) == []


def test_members_past_the_purchased_seats_queue_a_real_sync(db, gateway, billing_org) -> None:
    organization = billing_org["organization"]
    install_subscription(db, billing_org, gateway, seats=1)
    add_member(db, organization)  # 2 members on 1 seat: under-billed
    db.flush()

    reported = seat_service.report_drift(db)
    db.flush()

    assert [d.direction for d in reported] == ["UNDER_BILLED"]
    job = db.execute(
        select(Job).where(Job.job_type == "billing.seat_sync", Job.organization_id == organization.id)
    ).scalar_one_or_none()
    assert job is not None, "the drift sweep must queue the sync it asks for"


def test_a_deactivated_member_frees_the_seat(db, gateway, billing_org) -> None:
    from app.services import seat_capacity_service

    organization = billing_org["organization"]
    install_subscription(db, billing_org, gateway, seats=2)
    member = add_member(db, organization)
    assert seat_capacity_service.seat_capacity(db, organization_id=organization.id).available == 0

    member.status = MembershipStatus.DEACTIVATED
    db.flush()
    assert seat_capacity_service.seat_capacity(db, organization_id=organization.id).available == 1
