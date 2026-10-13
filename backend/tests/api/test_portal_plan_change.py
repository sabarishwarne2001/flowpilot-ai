"""A subscriber's plan and seat pick opens the billing portal on that change (campaign session 4, F-344).

    pytest tests/api/test_portal_plan_change.py -q

The plan picker shows a seat field with every paid plan, and a subscriber's change is made in the
billing portal on the live subscription (F-253, F-340). The portal used to open on its home page:
the seats picked were dropped and the customer had to find the plan a second time. The portal
session now carries the plan and the seats, and Stripe opens it on the confirmation of exactly that
change, prorated. A pick below the seats in use, a free plan, or a pick with no live subscription is
refused before the gateway is called. Without a pick (managing the card, cancelling) the portal
opens on its home page, as before.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.billing.portal_service import EphemeralSession
from app.services.billing.stripe_gateway import SubscriptionUpdate
from tests.api.test_checkout_while_subscribed import _publish_paid_plans
from tests.api.test_seat_purchase_api import (  # noqa: F401 - fixture
    SUBSCRIPTION_ID,
    _headers,
    _on_business,
    fake_gateway,
)


@pytest.fixture()
def portal_calls(fake_gateway, monkeypatch) -> list[dict[str, Any]]:  # noqa: F811
    """What the portal was asked to open, recorded instead of calling Stripe."""
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", SecretStr("sk_test_portal_plan_change"))
    monkeypatch.setattr(settings, "BILLING_GATEWAY", "STRIPE", raising=False)
    calls: list[dict[str, Any]] = []

    def create_portal_session(**kwargs: Any) -> EphemeralSession:
        calls.append(kwargs)
        return EphemeralSession(url="https://billing.stripe.com/p/session/test_plan_change", expires_at=None,
                                kind="portal", stripe_session_id="bps_plan_change")

    fake_gateway.create_portal_session = create_portal_session  # type: ignore[attr-defined]
    return calls


def _portal(client, tenant, **pick: Any):
    return client.post(
        f"/api/v1/organizations/{tenant.organization.id}/billing/portal-session",
        json={"return_url": "http://localhost:3000/organizations/acme/billing", **pick},
        headers=_headers(tenant.owner.user),
    )


def test_the_portal_opens_on_the_picked_plan_and_seats(
    client, db_session: Session, tenant, fake_gateway, portal_calls  # noqa: F811
) -> None:
    _publish_paid_plans(db_session, tenant)
    _on_business(db_session, tenant, fake_gateway, seats=5)
    response = _portal(client, tenant, quota_tier_key="enterprise", seats=7)
    assert response.status_code == 201, response.text
    assert len(portal_calls) == 1
    assert portal_calls[0]["subscription_update"] == SubscriptionUpdate(
        subscription_id=SUBSCRIPTION_ID, price_id="price_test_enterprise", quantity=7)


def test_without_a_pick_the_portal_opens_on_its_home_page(
    client, db_session: Session, tenant, fake_gateway, portal_calls  # noqa: F811
) -> None:
    _publish_paid_plans(db_session, tenant)
    _on_business(db_session, tenant, fake_gateway, seats=5)
    response = _portal(client, tenant)
    assert response.status_code == 201, response.text
    assert portal_calls[0].get("subscription_update") is None


def test_a_pick_below_the_seats_in_use_is_refused(
    client, db_session: Session, tenant, fake_gateway, portal_calls  # noqa: F811
) -> None:
    _publish_paid_plans(db_session, tenant)
    _on_business(db_session, tenant, fake_gateway, seats=5)  # the tenant fixture has 5 members
    response = _portal(client, tenant, quota_tier_key="enterprise", seats=2)
    assert response.status_code == 400, response.text
    assert "in use" in response.text
    assert portal_calls == []


def test_a_free_plan_is_a_cancellation_not_a_pick(
    client, db_session: Session, tenant, fake_gateway, portal_calls  # noqa: F811
) -> None:
    _publish_paid_plans(db_session, tenant)
    _on_business(db_session, tenant, fake_gateway, seats=5)
    response = _portal(client, tenant, quota_tier_key="free", seats=5)
    assert response.status_code == 400, response.text
    assert portal_calls == []


def test_a_pick_without_a_live_subscription_is_refused(
    client, db_session: Session, tenant, fake_gateway, portal_calls  # noqa: F811
) -> None:
    _publish_paid_plans(db_session, tenant)
    _on_business(db_session, tenant, fake_gateway, seats=5)
    db_session.execute(text("UPDATE subscriptions SET status = 'canceled'::subscription_status, canceled_at = now()"))
    db_session.commit()
    response = _portal(client, tenant, quota_tier_key="enterprise", seats=7)
    assert response.status_code == 400, response.text
    assert "checkout" in response.text.lower()
    assert portal_calls == []
