"""One Dodo subscription's whole life, delivered all at once (website and payments session, C3).

    pytest tests/engines/test_dodo_concurrent_deliveries_live.py -q

Dodo retries and does not promise order, and a busy deployment runs more than one `--loop billing`
worker. Here a real uvicorn server receives every webhook of one subscription's life (checkout,
three renewals, an upgrade, a cancellation at the period's end), each sent twice, sixteen at a time,
and four worker threads reconcile them at once, as four workers would, against the stand-in Dodo
over HTTP. Every delivery is answered 2xx, each webhook is stored once, nothing dead-letters, there
is one subscription row whose state is Dodo's final state, and each successful payment is invoiced
exactly once.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, update

from app.core.config import settings
from app.models.billing_account import BillingAccount
from app.models.invoice import Invoice
from app.models.stripe_inbound_event import StripeInboundEvent, StripeInboundStatus
from app.models.subscription import Subscription, SubscriptionStatus
from app.services.billing import inbound_service
from app.services.billing.dodo_gateway import reset_dodo_gateway
from tests.engines.test_concurrent_requests_do_not_freeze_the_api_live import (  # noqa: F401
    live_server,
)
from tests.fakes.fake_dodo import DEFAULT_SECRET, FakeDodo
from tests.services.test_dodo_reconciliation import BUSINESS_PRICE, DEVELOPER_PRICE, make_org

WEBHOOK = "/api/v1/billing/webhooks/dodo"


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
        yield fake
        reset_dodo_gateway()


def _worker(name: str, stop: threading.Event, errors: list[str]) -> None:
    """One `--loop billing` worker: claim a small batch, reconcile each row, repeat."""
    from app.db import session as session_module
    from app.workers.handlers.billing import _reconcile_claimed_row

    while not stop.is_set():
        try:
            with session_module.SessionLocal() as db:
                claimed = inbound_service.claim_batch(db, worker_id=name, batch_size=2, lease_seconds=60)
                batch = [(row.id, row.attempts, row.max_attempts) for row in claimed]
                db.commit()
            for event_id, attempts, max_attempts in batch:
                _reconcile_claimed_row(event_id, attempts=attempts, max_attempts=max_attempts)
            if not batch:
                stop.wait(0.05)
        except Exception as exc:  # noqa: BLE001 - recorded; the assertions report it
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            stop.wait(0.05)


def test_one_subscriptions_whole_life_delivered_at_once_settles_to_dodos_state(
    live_server: str, db_session, dodo: FakeDodo
) -> None:
    org = make_org(db_session)
    organization_id = org["organization"].id
    db_session.commit()  # the test's own session holds nothing while the server runs

    # Dodo's side of the life, recorded first: nothing has reached Servabo yet.
    start = datetime.now(timezone.utc)
    dodo.clock = lambda: start
    checkout = dodo.checkout(product_id="pdt_developer", quantity=2, email=org["owner"].email,
                             metadata={"organization_id": str(organization_id), "quota_tier_key": "developer"})
    sub_id = dodo.pay(checkout)["subscription_id"]
    for month in (1, 2, 3):
        moment = start + timedelta(days=30 * month + 1)
        dodo.clock = lambda moment=moment: moment
        dodo.renew(sub_id)
    dodo.clock = lambda: start + timedelta(days=100)
    dodo.handle("POST", f"/subscriptions/{sub_id}/change-plan",
                {"product_id": "pdt_business", "quantity": 3, "proration_billing_mode": "prorated_immediately"})
    dodo.cancel(sub_id, at_period_end=True)
    deliveries = list(dodo.outbox)
    dodo.outbox.clear()
    payments = [p for p in dodo.payments.values() if p["status"] == "succeeded"]
    assert len(deliveries) >= 10 and len(payments) == 5  # checkout, three renewals, the upgrade

    # Every webhook twice, sixteen at a time, in no particular order.
    copies = deliveries + deliveries[::-1]

    def send(delivery) -> int:
        with httpx.Client(timeout=30) as http:
            return http.post(f"{live_server}{WEBHOOK}", content=delivery.body, headers=delivery.headers).status_code

    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(send, copies, timeout=120))
    assert all(200 <= code < 300 for code in codes), codes

    ids = [d.headers["webhook-id"] for d in deliveries]
    stored = db_session.execute(select(func.count()).select_from(StripeInboundEvent)
                                .where(StripeInboundEvent.gateway_event_id.in_(ids))).scalar_one()
    assert stored == len(deliveries)  # a replay is never stored twice

    # Four workers reconcile at once until every webhook has settled.
    stop, errors = threading.Event(), []
    workers = [threading.Thread(target=_worker, args=(f"w{n}", stop, errors), daemon=True) for n in range(4)]
    for thread in workers:
        thread.start()
    terminal = (StripeInboundStatus.PROCESSED, StripeInboundStatus.IGNORED, StripeInboundStatus.DEAD)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=120)
    while datetime.now(timezone.utc) < deadline:
        db_session.rollback()
        rows = db_session.execute(select(StripeInboundEvent.status)
                                  .where(StripeInboundEvent.gateway_event_id.in_(ids))).scalars().all()
        if all(status in terminal for status in rows):
            break
        # A row a worker failed waits out its back-off; time passes for it here.
        db_session.execute(update(StripeInboundEvent).where(
            StripeInboundEvent.gateway_event_id.in_(ids), StripeInboundEvent.status == StripeInboundStatus.FAILED,
        ).values(available_at=datetime.now(timezone.utc)))
        db_session.commit()
        stop.wait(0.2)
    stop.set()
    for thread in workers:
        thread.join(timeout=30)
    assert errors == []

    db_session.rollback()
    outcomes = db_session.execute(select(StripeInboundEvent.event_type, StripeInboundEvent.status,
                                         StripeInboundEvent.last_error)
                                  .where(StripeInboundEvent.gateway_event_id.in_(ids))).all()
    assert all(o.status in (StripeInboundStatus.PROCESSED, StripeInboundStatus.IGNORED) for o in outcomes), outcomes

    # One subscription row, in Dodo's final state.
    [subscription] = db_session.execute(select(Subscription).where(Subscription.gateway_subscription_id == sub_id)
                                        ).scalars().all()
    final = dodo.subscriptions[sub_id]
    assert subscription.status is SubscriptionStatus.ACTIVE
    assert (subscription.quota_tier_key, subscription.seats_purchased) == ("business", final["quantity"])
    assert subscription.cancel_at_period_end is True
    account = db_session.execute(select(BillingAccount).where(BillingAccount.organization_id == organization_id)
                                 ).scalar_one()
    assert account.gateway_customer_id == final["customer_id"]

    # Each successful payment invoiced exactly once.
    invoiced = db_session.execute(select(Invoice.gateway_invoice_id).where(
        Invoice.billing_account_id == account.id)).scalars().all()
    assert sorted(invoiced) == sorted(p["payment_id"] for p in payments), invoiced
    assert len(set(invoiced)) == len(invoiced)
