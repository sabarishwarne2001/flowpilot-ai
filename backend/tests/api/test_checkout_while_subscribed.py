"""A paid organization is never sold a second subscription (campaign session 1, F).

    pytest tests/api/test_checkout_while_subscribed.py -q

The plan page offered "switching to a paid plan starts a new checkout; your current plan
stays active until it completes". A checkout creates a NEW subscription at the gateway,
which charges for it, while the database holds one live subscription per billing
account (`test_one_live_subscription_per_account`): the second would be charged and
never recorded, and the first would keep being charged too. So while a paid
subscription is live, a checkout for any paid plan is refused before the gateway is
called, with the same 409 the Free path uses and a message that says where plans are
changed. Once the subscription has ended, checkout works again.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.api.test_seat_purchase_api import _headers, _on_business, fake_gateway  # noqa: F401 - fixture


def _publish_paid_plans(db: Session, tenant) -> None:
    """Every paid plan on sale at its own gateway price, as production publishes them."""
    from datetime import datetime, timedelta, timezone

    from app.services import quota_service
    from tests.security.plans import _ensure_price_book, _seed

    _ensure_price_book(db)
    seed = _seed()
    for plan in ("developer", "business", "enterprise"):
        definition = seed.PLACEHOLDER_TIERS[plan]
        terms = seed.COMMERCIALS[plan]
        quota_service.publish_tier(
            db, key=plan, display_name=definition["display_name"], version=1,
            effective_from=datetime.now(timezone.utc) - timedelta(days=1),
            entries=seed._specs(definition["entries"]),  # noqa: SLF001 - the seed's own builder
            commercials=quota_service.TierCommercials(
                unit_amount_micros=terms["unit_amount_micros"], currency=terms["currency"],
                billing_interval=terms["billing_interval"], gateway_price_id=f"price_test_{plan}",
            ),
        )
    db.commit()
    quota_service.clear_cache()


def _checkout(client, tenant, plan: str):
    return client.post(
        f"/api/v1/organizations/{tenant.organization.id}/billing/checkout-session",
        json={"quota_tier_key": plan, "seats": 5},
        headers=_headers(tenant.owner.user),
    )


def test_a_live_paid_subscription_is_not_sold_a_second_one(
    client, db_session: Session, tenant, fake_gateway  # noqa: F811
) -> None:
    _publish_paid_plans(db_session, tenant)
    _on_business(db_session, tenant, fake_gateway, seats=3)
    for plan in ("developer", "business", "enterprise"):
        response = _checkout(client, tenant, plan)
        assert response.status_code == 409, (plan, response.text)
        body = response.json()
        body = body.get("detail", body)  # the same envelope as the Free path's 409
        assert body["code"] == "PAID_SUBSCRIPTION_ACTIVE"
        assert "billing portal" in body["message"]


def test_after_the_subscription_ends_checkout_is_open_again(
    client, db_session: Session, tenant, fake_gateway  # noqa: F811
) -> None:
    _publish_paid_plans(db_session, tenant)
    _on_business(db_session, tenant, fake_gateway, seats=3)
    db_session.execute(text("UPDATE subscriptions SET status = 'canceled'::subscription_status, canceled_at = now()"))
    db_session.commit()
    response = _checkout(client, tenant, "enterprise")
    assert response.status_code != 409, response.text
