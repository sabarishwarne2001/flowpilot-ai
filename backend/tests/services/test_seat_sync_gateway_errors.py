"""F-213 — "Reconcile seats" answered a 500 when Stripe could not be used.

    pytest tests/services/test_seat_sync_gateway_errors.py -q

Found by the live sweep: POST /organizations/{id}/billing/seats with no Stripe
secret key configured (every environment until the owner adds one, F-125) raised
StripeNotConfiguredError out of the route as a 500 with a traceback, and the seat
manager could only say "the change didn't go through". A Stripe outage or a
subscription Stripe no longer knows did the same. Checkout and the portal already
answer these with a 503 that names the problem; seat sync now does too, and
nothing is recorded as changed.
"""

from __future__ import annotations

import pytest

from app.core.security import create_access_token
from app.services.billing.stripe_gateway import (
    StripeNotConfiguredError,
    StripeObjectNotFoundError,
    StripeTransientError,
)
from tests.services.test_arch15_gate_15_1_15_2_inbound import (  # noqa: F401
    FakeStripeGateway,
    billing_org,
    gateway,
    stripe_settings,
)
from tests.services.test_arch15_gate_15_3_15_4_subscriptions_seats import (  # noqa: F401
    add_member,
    install_subscription,
)


@pytest.mark.parametrize(
    "error,status,code",
    [
        (StripeNotConfiguredError("STRIPE_SECRET_KEY is not set."), 503, "BILLING_GATEWAY_NOT_CONFIGURED"),
        (StripeTransientError("connection reset"), 503, "BILLING_GATEWAY_UNAVAILABLE"),
        (StripeObjectNotFoundError("no such subscription"), 409, "BILLING_GATEWAY_REFUSED"),
    ],
)
def test_seat_sync_answers_a_gateway_failure_with_a_reason_not_a_500(
    client, db, gateway: FakeStripeGateway, billing_org, monkeypatch, error, status, code
) -> None:
    install_subscription(db, billing_org, gateway, seats=1)
    add_member(db, billing_org["organization"])  # drift: two members, one seat
    db.commit()

    def refuse(**kwargs):
        raise error

    monkeypatch.setattr(gateway, "set_subscription_seats", refuse)
    token = create_access_token(subject=str(billing_org["owner"].id))
    response = client.post(
        f"/api/v1/organizations/{billing_org['organization'].id}/billing/seats",
        json={"reason": "owner_requested"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status, response.text
    body = response.json()
    assert body.get("code") == code or (body.get("detail") or {}).get("code") == code, body
