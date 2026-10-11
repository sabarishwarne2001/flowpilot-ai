"""Owners, admins and billing managers buy seats, after seeing the price (campaign session 1).

    pytest tests/api/test_seat_purchase_api.py -q

`PUT /organizations/{id}/billing/seats` sets the purchased seat quantity on the live
paid subscription. The price comes first from `GET .../billing/price-book/seat`; the
purchase carries the unit price that was shown and is refused at any other. The
Billing role can buy seats (as a billing manager can in GitHub or Atlassian); a member
cannot. The subscription state reports what the seat check enforces: capacity, seats
used (members and pending invitations), and whether the caller may change seats.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core import security
from app.models.billing_account import BillingAccount
from app.models.organization import OrganizationMember, OrganizationRole
from app.models.price_book import PriceBook
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User
from app.services.billing import stripe_gateway
from tests.security.plans import put_on_plan
from tests.services.test_arch15_gate_15_1_15_2_inbound import FakeStripeGateway, make_subscription

SUBSCRIPTION_ID = "sub_seat_api"
BUSINESS_SEAT_MICROS = 299_000_000


@pytest.fixture()
def fake_gateway():
    fake = FakeStripeGateway()
    previous = stripe_gateway.set_gateway(fake)
    try:
        yield fake
    finally:
        stripe_gateway.set_gateway(previous)


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {security.create_access_token(subject=str(user.id))}"}


def _billing_manager(db: Session, tenant) -> User:
    user = User(
        email=f"billing-{uuid.uuid4().hex[:8]}@acme.com",
        hashed_password=security.get_password_hash("test-password"),
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    db.add(OrganizationMember(organization_id=tenant.organization.id, user_id=user.id, role=OrganizationRole.BILLING))
    db.commit()
    return user


def _on_business(db: Session, tenant, fake_gateway, *, seats: int) -> None:
    tier = put_on_plan(db, tenant.organization, "business")
    customer = f"cus_{uuid.uuid4().hex[:10]}"
    account = BillingAccount(
        organization_id=tenant.organization.id, gateway="STRIPE", stripe_customer_id=customer,
        gateway_customer_id=customer, currency="USD", billing_email="billing@acme.com",
    )
    db.add(account)
    db.flush()
    now = datetime.now(timezone.utc)
    db.add(
        Subscription(
            billing_account_id=account.id, gateway="STRIPE", stripe_subscription_id=SUBSCRIPTION_ID,
            gateway_subscription_id=SUBSCRIPTION_ID, status=SubscriptionStatus.ACTIVE,
            quota_tier_key="business", quota_tier_id=tier.id, price_book_id=db.query(PriceBook).first().id,
            seats_purchased=seats, current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=29),
        )
    )
    db.commit()
    fake_gateway.current[SUBSCRIPTION_ID] = make_subscription(
        subscription_id=SUBSCRIPTION_ID, customer_id=customer, seats=seats, tier_key="business",
        period_start=now - timedelta(days=1), period_end=now + timedelta(days=29),
    )


def test_a_billing_manager_sees_the_price_then_buys_a_seat(client, db_session: Session, tenant, fake_gateway) -> None:
    # The tenant fixture has 5 members; 5 seats bought: full.
    _on_business(db_session, tenant, fake_gateway, seats=5)
    billing = _billing_manager(db_session, tenant)  # a 6th member (seeded past the check)
    org_id = tenant.organization.id

    state = client.get(f"/api/v1/organizations/{org_id}/billing/subscription", headers=_headers(billing))
    assert state.status_code == 200, state.text
    body = state.json()
    assert body["seat_capacity"] == 5
    assert body["seats_used"] == 6
    assert body["can_manage_seats"] is True

    quote = client.get(
        f"/api/v1/organizations/{org_id}/billing/price-book/seat?additional_seats=2", headers=_headers(billing)
    )
    assert quote.status_code == 200, quote.text
    assert quote.json()["unit_price_micros"] == BUSINESS_SEAT_MICROS

    bought = client.put(
        f"/api/v1/organizations/{org_id}/billing/seats",
        json={"seats": 7, "confirmed_unit_price_micros": BUSINESS_SEAT_MICROS},
        headers=_headers(billing),
    )
    assert bought.status_code == 200, bought.text
    assert bought.json()["seats_purchased"] == 7
    assert bought.json()["seats_available"] == 1
    assert fake_gateway.seat_calls[-1][:2] == (SUBSCRIPTION_ID, 7)


def test_a_purchase_at_a_stale_price_is_refused(client, db_session: Session, tenant, fake_gateway) -> None:
    _on_business(db_session, tenant, fake_gateway, seats=5)
    response = client.put(
        f"/api/v1/organizations/{tenant.organization.id}/billing/seats",
        json={"seats": 6, "confirmed_unit_price_micros": 1},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "SEAT_PRICE_CHANGED"
    assert fake_gateway.seat_calls == []


def test_seats_held_by_people_cannot_be_released(client, db_session: Session, tenant, fake_gateway) -> None:
    _on_business(db_session, tenant, fake_gateway, seats=8)
    response = client.put(
        f"/api/v1/organizations/{tenant.organization.id}/billing/seats",
        json={"seats": 4},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "SEATS_IN_USE"
    assert response.json()["details"]["seats_used"] == 5

    released = client.put(
        f"/api/v1/organizations/{tenant.organization.id}/billing/seats",
        json={"seats": 5},
        headers=tenant.owner.headers,
    )
    assert released.status_code == 200, released.text
    assert released.json()["seats_purchased"] == 5


def test_a_member_cannot_buy_seats(client, db_session: Session, tenant, fake_gateway) -> None:
    _on_business(db_session, tenant, fake_gateway, seats=5)
    response = client.put(
        f"/api/v1/organizations/{tenant.organization.id}/billing/seats",
        json={"seats": 6, "confirmed_unit_price_micros": BUSINESS_SEAT_MICROS},
        headers=tenant.contributor.headers,
    )
    assert response.status_code in (403, 404), response.text
    assert fake_gateway.seat_calls == []


def test_free_has_no_seats_to_buy(client, db_session: Session, tenant, fake_gateway) -> None:
    put_on_plan(db_session, tenant.organization, "free")
    response = client.put(
        f"/api/v1/organizations/{tenant.organization.id}/billing/seats",
        json={"seats": 3},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "NO_PAID_SUBSCRIPTION"


def test_checkout_cannot_sell_fewer_seats_than_people_in_the_organization(client, db_session: Session, tenant) -> None:
    """Five members: a checkout for one seat would leave the organization unable to add anyone."""
    put_on_plan(db_session, tenant.organization, "free")
    put_on_plan(db_session, tenant.organization, "business")  # publish Business (the plan on sale)
    put_on_plan(db_session, tenant.organization, "free")
    response = client.post(
        f"/api/v1/organizations/{tenant.organization.id}/billing/checkout-session",
        json={
            "quota_tier_key": "business",
            "seats": 1,
            "success_url": "http://localhost:3000/ok",
            "cancel_url": "http://localhost:3000/cancel",
        },
        headers=tenant.owner.headers,
    )
    assert response.status_code == 400, response.text
    assert "5 seats are in use" in response.text
