"""Dodo Payments reconciliation, proven end to end (website and payments session, C3).

`app/services/billing/dodo_reconcile_service.py` had no behaviour test. Here every event it handles
travels the real path: the stand-in Dodo (`tests/fakes/fake_dodo.py`, a real HTTP server) sends a
Standard Webhooks-signed event to the real `/api/v1/billing/webhooks/dodo` endpoint, which stores
it; the reconciler then re-fetches the subscription from the stand-in over HTTP through the real
adapter (D-9) and writes Servabo's state. Nothing is mocked between the webhook and the database.

Covered: subscription.active, renewed, on_hold, failed, cancelled, expired, plan_changed and
updated; payment.succeeded and payment.failed; deliveries out of order and replayed; the on-hold
grace ending in read-only, and recovery; add-on subscriptions (custom domain, warehouse sync);
adoption of the customer Dodo creates at checkout, including two checkouts racing.
"""

from __future__ import annotations

import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core import entitlements
from app.core.config import settings
from app.models.billing_account import BillingAccount
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.organization_addon import OrganizationAddon
from app.models.price_book import PriceBook, PriceBookEntry
from app.models.quota_tier import QuotaTier
from app.models.stripe_inbound_event import StripeInboundEvent, StripeInboundStatus
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User
from app.services.billing import dodo_reconcile_service, dunning_service, entitlement_service, inbound_service
from app.services.billing.dodo_gateway import reset_dodo_gateway
from tests.fakes.fake_dodo import DEFAULT_SECRET, FakeDodo

#: The plans' Dodo products, in cents per seat per month.
BUSINESS_PRICE, DEVELOPER_PRICE = 29_900, 4_900


@pytest.fixture()
def dodo(monkeypatch: pytest.MonkeyPatch):
    fake = FakeDodo(webhook_secret=DEFAULT_SECRET)
    with fake.serving() as base_url:
        monkeypatch.setattr(settings, "BILLING_GATEWAY", "DODO")
        monkeypatch.setattr(settings, "DODO_API_BASE", base_url)
        monkeypatch.setattr(settings, "DODO_API_KEY", SecretStr("dodo_test_fake_key_0123456789"))
        monkeypatch.setattr(settings, "DODO_WEBHOOK_SECRET", SecretStr(DEFAULT_SECRET))
        monkeypatch.setattr(settings, "DODO_LIVEMODE", False)
        reset_dodo_gateway()
        fake.add_product(name="Business", price=BUSINESS_PRICE, product_id="pdt_business")
        fake.add_product(name="Developer", price=DEVELOPER_PRICE, product_id="pdt_developer")
        fake.add_product(name="Custom domains", price=19_900, product_id="pdt_custom_domain")
        fake.add_product(name="Warehouse sync", price=30_000, product_id="pdt_warehouse_sync")
        monkeypatch.setattr(settings, "DODO_ADDON_PRODUCT_CUSTOM_DOMAIN", "pdt_custom_domain")
        monkeypatch.setattr(settings, "DODO_ADDON_PRODUCT_WAREHOUSE_SYNC", "pdt_warehouse_sync")
        yield fake
    reset_dodo_gateway()


def _tier(db, key: str, name: str, product_id: str | None, at: datetime, cents: int = 0) -> QuotaTier:
    priced = {"unit_amount_micros": cents * 10_000, "currency": "USD", "billing_interval": "month"} if product_id else {}
    tier = QuotaTier(key=key, display_name=name, version=random.randint(100, 10_000), effective_from=at,
                     published_at=None, is_active=False, gateway_price_id=product_id, **priced)
    db.add(tier)
    db.flush()
    tier.published_at = at
    tier.is_active = True
    db.flush([tier])
    return tier


@pytest.fixture()
def org(db):
    """An organization with no billing account yet: Dodo's checkout creates the customer (D-10)."""
    suffix = uuid.uuid4().hex[:8]
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    owner = User(email=f"owner-{suffix}@acme.test", hashed_password="!x", is_active=True, email_verified_at=at)
    organization = Organization(slug=f"acme-{suffix}", name="Acme", status=OrganizationStatus.ACTIVE)
    db.add_all([owner, organization])
    db.flush()
    db.add(OrganizationMember(organization_id=organization.id, user_id=owner.id, role=OrganizationRole.OWNER,
                              status=MembershipStatus.ACTIVE))
    book = PriceBook(version=(int(time.time() * 1000) + random.randint(1, 10**6)) % 10**7, effective_from=at,
                     currency="USD", published_at=None, content_digest=None, is_active=False)
    db.add(book)
    db.flush()
    db.add(PriceBookEntry(price_book_id=book.id, event_type=settings.BILLING_SEAT_EVENT_TYPE, provider="internal",
                          unit="seat", unit_price_micros=Decimal(BUSINESS_PRICE * 10_000)))
    db.flush()
    book.content_digest = "0" * 64
    book.published_at = at
    book.is_active = True
    db.flush([book])
    _tier(db, "business", "Business", "pdt_business", at, BUSINESS_PRICE)
    _tier(db, "developer", "Developer", "pdt_developer", at, DEVELOPER_PRICE)
    _tier(db, "free", "Free", None, at)
    db.flush()
    from app.services import pricing_service, quota_service

    # Plans and prices are cached per process: a tier an earlier test read must not stand in for these.
    quota_service.clear_cache()
    pricing_service.clear_cache()
    return {"organization": organization, "owner": owner}


def drain(db) -> list[tuple[str, str]]:
    """Reconcile every stored event as the `--loop billing` worker does; (event type, outcome)."""
    outcomes = []
    for row in inbound_service.claim_batch(db, worker_id="dodo-c3", batch_size=100):
        try:
            with db.begin_nested():  # the worker's own transaction per event
                outcome = dodo_reconcile_service.reconcile_row(db, row)
        except Exception as exc:  # noqa: BLE001 - recorded as the worker records it
            inbound_service.mark_failed(db, row.id, attempts=row.max_attempts, max_attempts=row.max_attempts,
                                        error=f"{type(exc).__name__}: {exc}")
            outcomes.append((row.event_type, f"failed: {exc}"))
            continue
        if outcome.handled:
            inbound_service.mark_processed(db, row.id, result={"ok": True})
            outcomes.append((row.event_type, "processed"))
        else:
            inbound_service.mark_ignored(db, row.id, reason=outcome.ignored_reason or "unhandled")
            outcomes.append((row.event_type, f"ignored: {outcome.ignored_reason}"))
    db.flush()
    return outcomes


def deliver(dodo: FakeDodo, client, db) -> list[tuple[str, str]]:
    responses = dodo.deliver(client)
    assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]
    return drain(db)


def subscribe(dodo: FakeDodo, client, db, org, *, product: str = "pdt_business", seats: int = 3,
              metadata: dict | None = None) -> str:
    checkout = dodo.checkout(product_id=product, quantity=seats, email=org["owner"].email,
                             metadata={"organization_id": str(org["organization"].id), **(metadata or {})})
    sub = dodo.pay(checkout)
    deliver(dodo, client, db)
    return sub["subscription_id"]


def row(db, subscription_id: str) -> Subscription:
    db.expire_all()
    return db.execute(select(Subscription).where(Subscription.gateway_subscription_id == subscription_id)).scalar_one()


# ---------------------------------------------------------------------------- checkout and adoption


def test_paying_at_checkout_creates_the_account_and_an_active_subscription(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org)
    subscription = row(db, sub_id)
    assert subscription.status is SubscriptionStatus.ACTIVE
    assert (subscription.quota_tier_key, subscription.seats_purchased, subscription.gateway) == ("business", 3, "DODO")
    account = db.execute(select(BillingAccount).where(
        BillingAccount.organization_id == org["organization"].id)).scalar_one()
    assert account.gateway == "DODO"
    assert account.gateway_customer_id == dodo.subscriptions[sub_id]["customer_id"]
    # The adapter re-fetched the subscription from Dodo rather than trusting the event body (D-9).
    assert ("GET", f"/subscriptions/{sub_id}", None) in dodo.calls


def test_a_declined_card_at_checkout_grants_nothing(dodo, client, db, org) -> None:
    checkout = dodo.checkout(product_id="pdt_business", quantity=1, email=org["owner"].email,
                             metadata={"organization_id": str(org["organization"].id)})
    sub = dodo.pay(checkout, succeed=False)
    outcomes = deliver(dodo, client, db)
    assert ("subscription.failed", "processed") in outcomes
    assert row(db, sub["subscription_id"]).status is SubscriptionStatus.INCOMPLETE_EXPIRED


@pytest.mark.parametrize("second_email", ["same", "another"])
def test_two_checkouts_racing_record_one_subscription_and_alert_the_operator(dodo, client, db, org, second_email) -> None:
    """Two tabs, two payments: Servabo keeps one live subscription and tells the operator to refund
    the other, as the Stripe path does, instead of failing the event until it dead-letters. With
    another email at the second checkout Dodo makes a second customer too."""
    meta = {"organization_id": str(org["organization"].id)}
    first = dodo.checkout(product_id="pdt_business", quantity=1, email=org["owner"].email, metadata=meta)
    second = dodo.checkout(product_id="pdt_business", quantity=1, metadata=meta,
                           email=org["owner"].email if second_email == "same" else "finance@acme.test")
    a = dodo.pay(first)
    b = dodo.pay(second)
    outcomes = deliver(dodo, client, db)
    assert not [o for o in outcomes if o[1].startswith("failed")], outcomes
    # Whichever payment's events the worker reconciles first is kept (in this test all four rows
    # share one transaction's timestamp, so either can be first); the other is flagged for refund.
    live = db.execute(select(Subscription).where(Subscription.gateway == "DODO",
                                                 Subscription.status == SubscriptionStatus.ACTIVE)).scalars().all()
    assert len(live) == 1 and live[0].gateway_subscription_id in (a["subscription_id"], b["subscription_id"])
    duplicate = b["subscription_id"] if live[0].gateway_subscription_id == a["subscription_id"] else a["subscription_id"]
    from app.models.operator_alert import OperatorAlert

    alerts = db.execute(select(OperatorAlert).where(OperatorAlert.kind == "DUPLICATE_SUBSCRIPTION")).scalars().all()
    assert len(alerts) == 1 and duplicate in alerts[0].body


def test_a_customer_bound_to_the_organization_is_never_re_pointed(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org)
    customer = dodo.subscriptions[sub_id]["customer_id"]
    account = db.execute(select(BillingAccount).where(
        BillingAccount.organization_id == org["organization"].id)).scalar_one()
    # A later renewal keeps the same customer; the account is not touched.
    dodo.renew(sub_id)
    deliver(dodo, client, db)
    db.refresh(account)
    assert account.gateway_customer_id == customer


# ---------------------------------------------------------------------------- the subscription's life


def test_a_renewal_moves_the_period_forward(dodo, client, db, org, monkeypatch) -> None:
    sub_id = subscribe(dodo, client, db, org)
    before = row(db, sub_id).current_period_end
    later = datetime.now(timezone.utc) + timedelta(days=31)
    dodo.clock = lambda: later
    dodo.renew(sub_id)
    outcomes = deliver(dodo, client, db)
    assert ("subscription.renewed", "processed") in outcomes and ("payment.succeeded", "processed") in outcomes
    assert row(db, sub_id).current_period_end > before
    assert row(db, sub_id).status is SubscriptionStatus.ACTIVE


def test_a_failed_renewal_holds_full_access_through_grace_then_read_only_then_recovery(
    dodo, client, db, org, monkeypatch
) -> None:
    organization_id = org["organization"].id
    sub_id = subscribe(dodo, client, db, org)
    dodo.renew(sub_id, succeed=False)
    outcomes = deliver(dodo, client, db)
    assert ("subscription.on_hold", "processed") in outcomes and ("payment.failed", "processed") in outcomes
    held = row(db, sub_id)
    assert held.status is SubscriptionStatus.PAST_DUE and held.grace_ends_at is not None
    assert dunning_service.access_state(db, organization_id=organization_id) is dunning_service.BillingAccessState.ACTIVE

    # The grace window passes with no further webhook: read-only on time.
    past_grace = held.grace_ends_at + timedelta(minutes=1)
    monkeypatch.setattr(dunning_service, "datetime", _FrozenDatetime.at(past_grace))
    assert dunning_service.access_state(db, organization_id=organization_id) is dunning_service.BillingAccessState.RESTRICTED

    # Another on_hold after grace moves the row to unpaid (live, not entitled).
    monkeypatch.setattr(entitlement_service, "utcnow", lambda: past_grace)
    dodo.renew(sub_id, succeed=False)
    deliver(dodo, client, db)
    assert row(db, sub_id).status is SubscriptionStatus.UNPAID

    # The card is fixed in the portal: active again, grace cleared, access restored.
    dodo.fix_card(sub_id)
    deliver(dodo, client, db)
    recovered = row(db, sub_id)
    assert recovered.status is SubscriptionStatus.ACTIVE and recovered.grace_ends_at is None
    assert dunning_service.access_state(db, organization_id=organization_id) is dunning_service.BillingAccessState.ACTIVE


def test_cancelling_at_the_period_end_keeps_the_plan_until_then(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org)
    dodo.cancel(sub_id, at_period_end=True)
    assert ("subscription.updated", "processed") in deliver(dodo, client, db)
    subscription = row(db, sub_id)
    assert subscription.status is SubscriptionStatus.ACTIVE and subscription.cancel_at_period_end is True
    assert subscription.cancel_at == subscription.current_period_end


@pytest.mark.parametrize("ending", ["cancel", "expire"])
def test_a_cancelled_or_expired_subscription_ends(dodo, client, db, org, ending: str) -> None:
    sub_id = subscribe(dodo, client, db, org)
    if ending == "cancel":
        dodo.cancel(sub_id, at_period_end=False)
    else:
        dodo.expire(sub_id)
    outcomes = deliver(dodo, client, db)
    assert (f"subscription.{'cancelled' if ending == 'cancel' else 'expired'}", "processed") in outcomes
    subscription = row(db, sub_id)
    assert subscription.status is SubscriptionStatus.CANCELED and subscription.canceled_at is not None


def test_a_plan_change_moves_the_tier_and_a_seat_change_the_seats(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org, seats=2)
    client_side = dodo.handle("POST", f"/subscriptions/{sub_id}/change-plan",
                              {"product_id": "pdt_developer", "quantity": 2, "proration_billing_mode": "do_not_bill"})
    assert client_side[0] == 200
    assert ("subscription.plan_changed", "processed") in deliver(dodo, client, db)
    assert row(db, sub_id).quota_tier_key == "developer"

    dodo.handle("POST", f"/subscriptions/{sub_id}/change-plan",
                {"product_id": "pdt_developer", "quantity": 5, "proration_billing_mode": "do_not_bill"})
    deliver(dodo, client, db)
    assert row(db, sub_id).seats_purchased == 5


def test_a_subscription_updated_event_applies_what_dodo_now_says(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org, seats=1)
    dodo.subscriptions[sub_id]["quantity"] = 4
    dodo.handle("PATCH", f"/subscriptions/{sub_id}", {"cancel_at_next_billing_date": False})
    assert ("subscription.updated", "processed") in deliver(dodo, client, db)
    assert row(db, sub_id).seats_purchased == 4


# ---------------------------------------------------------------------------- order and replay


def test_a_late_on_hold_cannot_suspend_a_customer_who_already_paid(dodo, client, db, org) -> None:
    """Dodo does not promise order. The on_hold arrives after the card was fixed: the re-fetch says
    active, so the customer stays active (D-9)."""
    sub_id = subscribe(dodo, client, db, org)
    dodo.renew(sub_id, succeed=False)
    late = list(dodo.outbox)
    dodo.outbox.clear()
    dodo.fix_card(sub_id)
    deliver(dodo, client, db)
    assert row(db, sub_id).status is SubscriptionStatus.ACTIVE
    dodo.outbox.extend(late)
    deliver(dodo, client, db)
    assert row(db, sub_id).status is SubscriptionStatus.ACTIVE


def test_a_replayed_delivery_is_stored_once_and_applied_once(dodo, client, db, org) -> None:
    checkout = dodo.checkout(product_id="pdt_business", quantity=1, email=org["owner"].email,
                             metadata={"organization_id": str(org["organization"].id)})
    dodo.pay(checkout)
    copies = list(dodo.outbox)
    deliver(dodo, client, db)
    for delivery in copies:  # Dodo retries the same message id
        assert client.post("/api/v1/billing/webhooks/dodo", content=delivery.body, headers=delivery.headers).status_code == 200
    rows = db.execute(select(StripeInboundEvent).where(StripeInboundEvent.gateway == "DODO")).scalars().all()
    assert len(rows) == len(copies)
    assert drain(db) == []
    assert all(r.status in (StripeInboundStatus.PROCESSED, StripeInboundStatus.IGNORED) for r in rows)


# ---------------------------------------------------------------------------- add-ons


@pytest.mark.parametrize("addon,product", [
    (entitlements.CUSTOM_DOMAIN_ADDON, "pdt_custom_domain"),
    (entitlements.WAREHOUSE_SYNC_ADDON, "pdt_warehouse_sync"),
])
def test_an_add_on_subscription_is_recorded_on_the_add_on_ledger(dodo, client, db, org, addon, product) -> None:
    subscribe(dodo, client, db, org)  # the plan first
    sub_id = subscribe(dodo, client, db, org, product=product, seats=1, metadata={"addon_key": addon})
    ledger = db.execute(select(OrganizationAddon).where(
        OrganizationAddon.organization_id == org["organization"].id, OrganizationAddon.addon_key == addon)).scalar_one()
    assert (ledger.gateway, ledger.gateway_subscription_id, ledger.purchase_status) == ("DODO", sub_id, "ACTIVE")

    dodo.cancel(sub_id, at_period_end=False)
    deliver(dodo, client, db)
    db.refresh(ledger)
    assert ledger.purchase_status != "ACTIVE"
    # The plan's own subscription is untouched by the add-on's.
    plans = db.execute(select(Subscription).where(Subscription.gateway == "DODO")).scalars().all()
    assert [s.status for s in plans] == [SubscriptionStatus.ACTIVE]


class _FrozenDatetime(datetime):
    """`datetime` whose now() is fixed (dunning_service reads datetime.now)."""

    _at: datetime

    @classmethod
    def at(cls, moment: datetime) -> type:
        return type("Frozen", (cls,), {"_at": moment})

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls._at
