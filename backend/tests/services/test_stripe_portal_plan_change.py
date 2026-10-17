"""The Stripe portal session for a picked plan opens on the confirmation of that change (F-344).

    pytest tests/services/test_stripe_portal_plan_change.py -q

Stripe's `flow_data.subscription_update_confirm` opens the customer portal on one change of a
subscription item (its price and quantity), shows the prorated amount, and returns the customer to
Servabo when they confirm. The gateway names the subscription's licensed item, which it reads
from Stripe, since an item id Servabo does not hold cannot be guessed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.billing.stripe_gateway import (
    StripeGateway,
    StripePermanentError,
    SubscriptionUpdate,
)
from tests.services.test_arch15_gate_15_1_15_2_inbound import make_subscription


class _Sessions:
    def __init__(self) -> None:
        self.params: list[dict[str, Any]] = []

    def create(self, params: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        self.params.append(params)
        return {"id": "bps_test", "url": "https://billing.stripe.com/p/session/test_flow"}


class _Subscriptions:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw

    def retrieve(self, subscription_id: str, params: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        assert subscription_id == self.raw["id"]
        return self.raw


def _gateway(raw: dict[str, Any]) -> tuple[StripeGateway, _Sessions]:
    sessions = _Sessions()
    gateway = StripeGateway(api_key="sk_test_portal_flow")
    gateway._client = SimpleNamespace(v1=SimpleNamespace(  # noqa: SLF001 - the SDK client, stood in
        billing_portal=SimpleNamespace(sessions=sessions), subscriptions=_Subscriptions(raw)))
    return gateway, sessions


def test_a_picked_plan_opens_the_portal_on_its_confirmation() -> None:
    gateway, sessions = _gateway(make_subscription(subscription_id="sub_flow", seats=5))
    session = gateway.create_portal_session(
        customer_id="cus_flow", return_url="https://app.example.com/organizations/acme/billing",
        subscription_update=SubscriptionUpdate(subscription_id="sub_flow", price_id="price_enterprise", quantity=7),
    )
    assert session.url == "https://billing.stripe.com/p/session/test_flow"
    assert sessions.params == [{
        "customer": "cus_flow",
        "return_url": "https://app.example.com/organizations/acme/billing",
        "flow_data": {
            "type": "subscription_update_confirm",
            "subscription_update_confirm": {
                "subscription": "sub_flow",
                "items": [{"id": "si_gate15", "price": "price_enterprise", "quantity": 7}],
            },
            "after_completion": {
                "type": "redirect",
                "redirect": {"return_url": "https://app.example.com/organizations/acme/billing"},
            },
        },
    }]


def test_without_a_pick_the_portal_opens_on_its_home_page() -> None:
    gateway, sessions = _gateway(make_subscription(subscription_id="sub_flow"))
    gateway.create_portal_session(customer_id="cus_flow", return_url="https://app.example.com/billing")
    assert sessions.params == [{"customer": "cus_flow", "return_url": "https://app.example.com/billing"}]


def test_a_subscription_with_no_licensed_item_is_refused() -> None:
    raw = make_subscription(subscription_id="sub_flow")
    raw["items"]["data"][0]["quantity"] = None  # a metered-only subscription has no seat quantity
    gateway, sessions = _gateway(raw)
    with pytest.raises(StripePermanentError):
        gateway.create_portal_session(
            customer_id="cus_flow", return_url=None,
            subscription_update=SubscriptionUpdate(subscription_id="sub_flow", price_id="price_x", quantity=3),
        )
    assert sessions.params == []
