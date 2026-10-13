"""A Stripe subscription that starts or ends tells the organization (campaign session 3, N-062).

The Dodo path announced every status and plan change ("Subscription ended", "Plan changed to
...") through `organization_notification_service`; the Stripe path wrote the new state and told
nobody, so an organization whose card subscription was cancelled dropped to Free in silence.
Payment failures stay with dunning, which already sends its own steps: announcing `past_due`
here as well would send two emails for one failed payment.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.models.notification import Notification
from app.models.notification_delivery import NotificationDelivery
from app.services.billing import subscription_service
from tests.services.test_arch15_gate_15_1_15_2_inbound import (  # noqa: F401
    FakeStripeGateway,
    billing_org,
    gateway,
    make_subscription,
    stripe_settings,
)


def _reconcile(db, billing_org, gateway, **fields) -> None:
    gateway.current["sub_gate15"] = make_subscription(**fields)
    _, applied = subscription_service.upsert_from_stripe(
        db, account=billing_org["account"], snapshot=gateway.fetch_subscription("sub_gate15")
    )
    assert applied
    db.flush()


def _titles(db, organization_id) -> list[str]:
    return list(
        db.execute(
            select(Notification.title).where(Notification.organization_id == organization_id).order_by(Notification.created_at)
        ).scalars()
    )


def test_a_new_subscription_and_its_end_are_announced_and_emailed(db, gateway, billing_org) -> None:
    organization = billing_org["organization"]
    _reconcile(db, billing_org, gateway, status="active")
    assert _titles(db, organization.id) == ["Subscription active"]

    _reconcile(db, billing_org, gateway, status="active", seats=5)
    assert _titles(db, organization.id) == ["Subscription active"], "a seat change is not a status change"

    _reconcile(db, billing_org, gateway, status="canceled", canceled_at=datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert _titles(db, organization.id) == ["Subscription active", "Subscription ended"]
    targets = db.execute(
        select(NotificationDelivery.target).where(NotificationDelivery.organization_id == organization.id)
    ).scalars().all()
    assert targets.count(billing_org["owner"].email) == 2


def test_a_failed_payment_is_left_to_dunning(db, gateway, billing_org) -> None:
    organization = billing_org["organization"]
    _reconcile(db, billing_org, gateway, status="active")
    _reconcile(db, billing_org, gateway, status="past_due")
    assert _titles(db, organization.id) == ["Subscription active"]

    _reconcile(db, billing_org, gateway, status="active")
    assert _titles(db, organization.id)[-1] == "Payment received"
