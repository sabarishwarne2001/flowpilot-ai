"""Gate 15.1 / 15.2 — the inbound door.

Asserts, in the plan's own words:

* a replayed `event.id` inserts once; the second POST returns 200, no new row
* a tampered body fails verification, returns 400, and persists nothing
* events delivered out of order converge to the same final state as in-order
  delivery — *asserting equality of the final row, not of the path*
* a handler that raises leaves the row FAILED with `attempts` incremented and
  `available_at` pushed out; the reaper releases an expired lease
* an event type we do not handle lands IGNORED, not PROCESSED
* test-mode events are refused in a live-mode deployment
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Generator, Optional

import pytest
from pydantic import SecretStr
from sqlalchemy import select, text, update

from app.core.config import settings
from app.models.billing_account import BillingAccount
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.price_book import PriceBook, PriceBookEntry
from app.models.quota_tier import QuotaTier, QuotaTierEntry
from app.models.stripe_inbound_event import (
    StripeInboundEvent,
    StripeInboundStatus,
)
from app.models.subscription import Subscription
from app.models.user import User
from app.services import quota_service
from app.services.billing import (
    inbound_service,
    reconcile_service,
    stripe_gateway,
)
from app.services.billing.stripe_gateway import (
    StripeCustomerSnapshot,
    StripeGateway,
    StripeInvoiceSnapshot,
    StripeObjectNotFoundError,
    StripeSubscriptionSnapshot,
)

WEBHOOK_URL = "/api/v1/billing/stripe/webhook"
TEST_SECRET = "whsec_gate15_primary"


# ============================================================================
# A gateway with no network
# ============================================================================


class FakeStripeGateway(StripeGateway):
    def __init__(self) -> None:
        super().__init__(api_key="sk_test_fake", api_version="2026-07-29.dahlia")
        self.current: dict[str, dict[str, Any]] = {}
        self.customers: dict[str, dict[str, Any]] = {}
        self.invoices: dict[str, dict[str, Any]] = {}
        self.fetch_calls: list[str] = []
        self.seat_calls: list[tuple[str, int, str]] = []
        self.fail_next_fetch: Optional[Exception] = None
        self._clock = 1_000_000

    def _tick(self) -> int:
        self._clock += 1
        return self._clock

    def fetch_subscription(self, subscription_id: str) -> StripeSubscriptionSnapshot:
        self.fetch_calls.append(subscription_id)
        if self.fail_next_fetch is not None:
            error, self.fail_next_fetch = self.fail_next_fetch, None
            raise error
        raw = self.current.get(subscription_id)
        if raw is None:
            raise StripeObjectNotFoundError(f"no such subscription {subscription_id}")
        return self._snapshot_subscription(raw, state_version=self._tick())

    def fetch_customer(self, customer_id: str) -> StripeCustomerSnapshot:
        raw = self.customers.get(customer_id)
        if raw is None:
            raise StripeObjectNotFoundError(f"no such customer {customer_id}")
        return StripeCustomerSnapshot(
            id=customer_id,
            email=raw.get("email"),
            currency=(raw.get("currency") or "usd").upper(),
            deleted=bool(raw.get("deleted")),
            metadata={k: str(v) for k, v in (raw.get("metadata") or {}).items()},
            raw=raw,
        )

    def fetch_invoice(self, invoice_id: str) -> StripeInvoiceSnapshot:
        raw = self.invoices.get(invoice_id, {})
        return StripeInvoiceSnapshot(
            id=invoice_id,
            customer_id=raw.get("customer", "cus_gate15"),
            subscription_id=raw.get("subscription", "sub_gate15"),
            status=raw.get("status", "paid"),
            currency=raw.get("currency", "USD"),
            total_cents=int(raw.get("total", 10000)),
            subtotal_cents=int(raw.get("subtotal", 10000)),
            tax_cents=int(raw.get("tax", 0)),
            amount_paid_cents=int(raw.get("amount_paid", 10000)),
            period_start=None,
            period_end=None,
            paid=bool(raw.get("paid", True)),
            raw=raw,
        )

    def fetch_invoice_total_cents(self, invoice_id: str) -> int:
        return self.fetch_invoice(invoice_id).total_cents

    def set_subscription_seats(
        self,
        *,
        subscription_id: str,
        seats: int,
        item_id: Optional[str] = None,
        proration_behavior: Optional[str] = None,
        reason: str = "seat_sync",
    ) -> StripeSubscriptionSnapshot:
        behavior = proration_behavior or settings.BILLING_SEAT_PRORATION_BEHAVIOR
        self.seat_calls.append((subscription_id, int(seats), behavior))
        raw = self.current[subscription_id]
        raw["items"]["data"][0]["quantity"] = int(seats)
        return self._snapshot_subscription(raw, state_version=self._tick())


def make_subscription(
    *,
    subscription_id: str = "sub_gate15",
    customer_id: str = "cus_gate15",
    status: str = "active",
    seats: int = 3,
    tier_key: str = "business",
    period_start: Optional[datetime] = None,
    period_end: Optional[datetime] = None,
    cancel_at_period_end: bool = False,
    canceled_at: Optional[datetime] = None,
) -> dict[str, Any]:
    start = period_start or datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = period_end or datetime(2026, 9, 1, tzinfo=timezone.utc)
    return {
        "id": subscription_id,
        "object": "subscription",
        "customer": customer_id,
        "status": status,
        "currency": "usd",
        "cancel_at_period_end": cancel_at_period_end,
        "canceled_at": int(canceled_at.timestamp()) if canceled_at else None,
        "metadata": {"quota_tier_key": tier_key},
        "items": {
            "object": "list",
            "data": [
                {
                    "id": "si_gate15",
                    "object": "subscription_item",
                    "quantity": seats,
                    "current_period_start": int(start.timestamp()),
                    "current_period_end": int(end.timestamp()),
                    "price": {"id": "price_seat", "currency": "usd"},
                }
            ],
        },
    }


def event_body(
    event_type: str,
    obj: dict[str, Any],
    *,
    event_id: Optional[str] = None,
    livemode: bool = False,
) -> bytes:
    return json.dumps(
        {
            "id": event_id or f"evt_{uuid.uuid4().hex[:16]}",
            "object": "event",
            "type": event_type,
            "api_version": "2026-07-29.dahlia",
            "created": int(time.time()),
            "livemode": livemode,
            "data": {"object": obj},
        }
    ).encode("utf-8")


def sign(body: bytes, *, secret: str = TEST_SECRET, timestamp: Optional[int] = None) -> str:
    stamp = timestamp if timestamp is not None else int(time.time())
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{stamp}".encode("utf-8") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return f"t={stamp},v1={digest}"


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def gateway() -> Generator[FakeStripeGateway, None, None]:
    fake = FakeStripeGateway()
    previous = stripe_gateway.set_gateway(fake)
    try:
        yield fake
    finally:
        stripe_gateway.set_gateway(previous)


@pytest.fixture(autouse=True)
def stripe_settings(monkeypatch):
    monkeypatch.setattr(
        settings, "STRIPE_WEBHOOK_SECRETS", SecretStr(TEST_SECRET), raising=False
    )
    monkeypatch.setattr(settings, "STRIPE_LIVEMODE", False, raising=False)
    monkeypatch.setattr(
        settings, "STRIPE_SECRET_KEY", SecretStr("sk_test_gate15"), raising=False
    )
    quota_service.clear_cache()
    yield
    quota_service.clear_cache()


@pytest.fixture()
def billing_org(db):
    """An organization with a published tier, a price book, and an account."""
    suffix = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc) - timedelta(days=30)

    owner = User(
        email=f"owner-{suffix}@acme.test",
        hashed_password="!x",
        is_active=True,
        email_verified_at=now,
    )
    db.add(owner)
    db.flush()

    org = Organization(
        slug=f"acme-{suffix}", name="Acme", status=OrganizationStatus.ACTIVE
    )
    db.add(org)
    db.flush()
    db.add(
        OrganizationMember(
            organization_id=org.id,
            user_id=owner.id,
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
    )

    book = PriceBook(
        version=(int(time.time() * 1000) + random.randint(1, 1000000)) % 10000000,
        effective_from=now,
        currency="USD",
        published_at=None,
        content_digest=None,
        is_active=False,
    )
    db.add(book)
    db.flush()
    db.add(
        PriceBookEntry(
            price_book_id=book.id,
            event_type=settings.BILLING_SEAT_EVENT_TYPE,
            provider="internal",
            unit="seat",
            unit_price_micros=Decimal("25000000"),
        )
    )
    db.add(
        PriceBookEntry(
            price_book_id=book.id,
            event_type="llm.input_token",
            provider="groq",
            unit="token",
            unit_price_micros=Decimal("2"),
        )
    )
    db.flush()
    book.content_digest = "0" * 64
    book.published_at = now
    book.is_active = True
    db.flush([book])

    tier = QuotaTier(
        key="business",
        display_name="Business",
        version=3,
        effective_from=now,
        published_at=None,
        is_active=False,
    )
    db.add(tier)
    db.flush()
    db.add(
        QuotaTierEntry(
            quota_tier_id=tier.id,
            limit_key="llm.input_token",
            max_quantity=1_000_000,
            overage_policy="ALLOW_AND_BILL",
            overage_price_tier_key="llm.input_token",
        )
    )
    db.flush()
    tier.published_at = now
    tier.is_active = True
    db.flush([tier])

    account = BillingAccount(
        organization_id=org.id,
        stripe_customer_id="cus_gate15",
        currency="USD",
        billing_email=f"billing-{suffix}@acme.test",
    )
    db.add(account)
    db.flush()
    db.commit()

    return {
        "organization": org,
        "owner": owner,
        "account": account,
        "tier": tier,
        "price_book": book,
    }


def drain(db, gateway_fake: FakeStripeGateway) -> list[StripeInboundEvent]:
    claimed = inbound_service.claim_batch(db, worker_id="gate15", batch_size=50)
    processed: list[StripeInboundEvent] = []

    for row in claimed:
        event = reconcile_service.event_from_row(row)
        try:
            outcome = reconcile_service.reconcile_event(db, event)
        except Exception as exc:  # noqa: BLE001
            inbound_service.mark_failed(
                db,
                row.id,
                attempts=row.attempts,
                max_attempts=row.max_attempts,
                error=f"{type(exc).__name__}: {exc}",
            )
            processed.append(row)
            continue

        if outcome.organization_id is not None:
            inbound_service.attach_organization(
                db, row.id, organization_id=outcome.organization_id
            )
        if outcome.handled:
            inbound_service.mark_processed(db, row.id, result={"ok": True})
        else:
            inbound_service.mark_ignored(
                db, row.id, reason=outcome.ignored_reason or "unhandled"
            )
        processed.append(row)

    db.flush()
    return processed


# ============================================================================
# Gate 15.1 — the endpoint
# ============================================================================


class TestGate151SignatureAndReplay:
    def test_valid_signature_is_accepted_and_persisted(self, client, db, gateway):
        body = event_body(
            "customer.subscription.created",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )
        response = client.post(
            WEBHOOK_URL, content=body, headers={"Stripe-Signature": sign(body)}
        )

        assert response.status_code == 200
        assert response.json() == {"received": True, "duplicate": False}

        row = db.execute(select(StripeInboundEvent)).scalar_one()
        assert row.status is StripeInboundStatus.PENDING
        assert row.event_type == "customer.subscription.created"
        assert row.processed_at is None
        assert row.signature_header.startswith("t=")

    def test_tampered_body_is_refused_and_persists_nothing(self, client, db, gateway):
        body = event_body(
            "customer.subscription.updated",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )
        header = sign(body)
        tampered = body.replace(b"sub_gate15", b"sub_attacker")

        response = client.post(
            WEBHOOK_URL, content=tampered, headers={"Stripe-Signature": header}
        )

        assert response.status_code == 400
        assert db.execute(select(StripeInboundEvent)).first() is None

    def test_missing_signature_header_is_refused(self, client, db, gateway):
        body = event_body("customer.created", {"object": "customer", "id": "cus_x"})
        response = client.post(WEBHOOK_URL, content=body)
        assert response.status_code == 400
        assert db.execute(select(StripeInboundEvent)).first() is None

    def test_replayed_event_id_inserts_once(self, client, db, gateway):
        body = event_body(
            "customer.subscription.updated",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
            event_id="evt_replay_me",
        )

        first = client.post(
            WEBHOOK_URL, content=body, headers={"Stripe-Signature": sign(body)}
        )
        second = client.post(
            WEBHOOK_URL, content=body, headers={"Stripe-Signature": sign(body)}
        )

        assert first.status_code == 200
        assert first.json()["duplicate"] is False
        assert second.status_code == 200
        assert second.json()["duplicate"] is True

        rows = db.execute(select(StripeInboundEvent)).scalars().all()
        assert len(rows) == 1

    def test_rotation_window_accepts_both_secrets(self, client, db, gateway, monkeypatch):
        monkeypatch.setattr(
            settings,
            "STRIPE_WEBHOOK_SECRETS",
            SecretStr(f"whsec_new_rotated,{TEST_SECRET}"),
            raising=False,
        )
        body = event_body("customer.created", {"object": "customer", "id": "cus_rot"})

        old_signed = client.post(
            WEBHOOK_URL,
            content=body,
            headers={"Stripe-Signature": sign(body, secret=TEST_SECRET)},
        )
        assert old_signed.status_code == 200

        body2 = event_body("customer.created", {"object": "customer", "id": "cus_rot2"})
        new_signed = client.post(
            WEBHOOK_URL,
            content=body2,
            headers={"Stripe-Signature": sign(body2, secret="whsec_new_rotated")},
        )
        assert new_signed.status_code == 200

    def test_stale_timestamp_is_refused(self, client, db, gateway):
        body = event_body("customer.created", {"object": "customer", "id": "cus_old"})
        stale = sign(body, timestamp=int(time.time()) - 4000)

        response = client.post(
            WEBHOOK_URL, content=body, headers={"Stripe-Signature": stale}
        )
        assert response.status_code == 400
        assert db.execute(select(StripeInboundEvent)).first() is None

    def test_test_mode_event_refused_in_live_mode_deployment(
        self, client, db, gateway, monkeypatch
    ):
        monkeypatch.setattr(settings, "STRIPE_LIVEMODE", True, raising=False)
        body = event_body(
            "customer.subscription.updated",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
            livemode=False,
        )

        response = client.post(
            WEBHOOK_URL, content=body, headers={"Stripe-Signature": sign(body)}
        )

        assert response.status_code == 400
        assert db.execute(select(StripeInboundEvent)).first() is None

    def test_oversized_body_refused_before_hmac(self, client, db, gateway, monkeypatch):
        monkeypatch.setattr(
            settings, "STRIPE_MAX_WEBHOOK_BODY_BYTES", 256, raising=False
        )
        body = event_body(
            "customer.created",
            {"object": "customer", "id": "cus_big", "padding": "x" * 4096},
        )
        response = client.post(
            WEBHOOK_URL, content=body, headers={"Stripe-Signature": sign(body)}
        )
        assert response.status_code == 413
        assert db.execute(select(StripeInboundEvent)).first() is None

    def test_handler_does_no_reconcile_work(self, client, db, gateway, billing_org):
        gateway.current["sub_gate15"] = make_subscription()
        body = event_body(
            "customer.subscription.updated",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )
        client.post(WEBHOOK_URL, content=body, headers={"Stripe-Signature": sign(body)})

        assert gateway.fetch_calls == []
        assert db.execute(select(Subscription)).first() is None


# ============================================================================
# Gate 15.2 — the claim path
# ============================================================================


class TestGate152Reconciliation:
    def _ingest(self, db, event_type: str, obj: dict[str, Any], **kwargs) -> uuid.UUID:
        body = event_body(event_type, obj, **kwargs)
        event = stripe_gateway.get_gateway().verify_event(
            payload=body, signature_header=sign(body)
        )
        row_id, _ = inbound_service.record_event(
            db, event=event, signature_header=sign(body)
        )
        db.flush()
        return row_id

    def test_out_of_order_delivery_converges_to_the_same_state(
        self, db, gateway, billing_org
    ):
        gateway.current["sub_gate15"] = make_subscription(seats=7, status="active")

        self._ingest(
            db,
            "customer.subscription.created",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )
        self._ingest(
            db,
            "customer.subscription.updated",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )
        drain(db, gateway)

        in_order = db.execute(select(Subscription)).scalar_one()
        in_order_state = (
            in_order.status.value,
            in_order.seats_purchased,
            in_order.current_period_start,
            in_order.current_period_end,
            in_order.quota_tier_id,
            in_order.price_book_id,
        )

        db.execute(text("DELETE FROM subscriptions"))
        db.execute(text("DELETE FROM stripe_inbound_events"))
        db.flush()

        self._ingest(
            db,
            "customer.subscription.updated",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )
        self._ingest(
            db,
            "customer.subscription.created",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )
        drain(db, gateway)

        reversed_order = db.execute(select(Subscription)).scalar_one()
        reversed_state = (
            reversed_order.status.value,
            reversed_order.seats_purchased,
            reversed_order.current_period_start,
            reversed_order.current_period_end,
            reversed_order.quota_tier_id,
            reversed_order.price_book_id,
        )

        assert in_order_state == reversed_state

    def test_stale_fetch_landing_last_does_not_regress_state(
        self, db, gateway, billing_org
    ):
        gateway.current["sub_gate15"] = make_subscription(seats=2)
        self._ingest(
            db,
            "customer.subscription.created",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )
        drain(db, gateway)

        subscription = db.execute(select(Subscription)).scalar_one()
        newer_version = subscription.stripe_state_version

        from app.services.billing import subscription_service

        stale = StripeSubscriptionSnapshot(
            id="sub_gate15",
            customer_id="cus_gate15",
            status="canceled",
            seats=99,
            current_period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            current_period_end=datetime(2026, 2, 1, tzinfo=timezone.utc),
            cancel_at_period_end=False,
            cancel_at=None,
            canceled_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
            trial_end=None,
            currency="USD",
            metadata={"quota_tier_key": "business"},
            state_version=newer_version - 5,
        )
        result, applied = subscription_service.upsert_from_stripe(
            db, account=billing_org["account"], snapshot=stale
        )

        assert applied is False
        db.refresh(subscription)
        assert subscription.seats_purchased == 2
        assert subscription.status.value == "active"
        assert subscription.stripe_state_version == newer_version

    def test_unhandled_event_type_lands_ignored_not_processed(
        self, db, gateway, billing_org
    ):
        row_id = self._ingest(
            db,
            "invoice.created",
            {"object": "invoice", "id": "in_1", "customer": "cus_gate15"},
        )
        drain(db, gateway)

        row = db.get(StripeInboundEvent, row_id)
        assert row.status is StripeInboundStatus.IGNORED
        assert row.status is not StripeInboundStatus.PROCESSED
        assert row.processed_at is not None
        assert row.result["ignored_reason"] == "not_yet_implemented"

    def test_completely_unknown_event_type_is_also_ignored(
        self, db, gateway, billing_org
    ):
        row_id = self._ingest(
            db,
            "radar.early_fraud_warning.created",
            {"object": "radar.early_fraud_warning", "id": "issfr_1", "customer": "cus_gate15"},
        )
        drain(db, gateway)

        row = db.get(StripeInboundEvent, row_id)
        assert row.status is StripeInboundStatus.IGNORED
        assert row.result["ignored_reason"] == "unsubscribed_event_type"

    def test_raising_handler_leaves_row_failed_with_backoff(
        self, db, gateway, billing_org
    ):
        gateway.current["sub_gate15"] = make_subscription()
        gateway.fail_next_fetch = stripe_gateway.StripeTransientError("boom")

        row_id = self._ingest(
            db,
            "customer.subscription.updated",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )
        before = db.get(StripeInboundEvent, row_id).attempts

        drain(db, gateway)

        row = db.get(StripeInboundEvent, row_id)
        db.refresh(row)
        assert row.status is StripeInboundStatus.FAILED
        assert row.attempts == before + 1
        assert row.available_at > datetime.now(timezone.utc)
        assert row.claim_expires_at is None
        assert "boom" in (row.last_error or "")

    def test_expired_lease_is_reaped_and_reclaimable(self, db, gateway, billing_org):
        gateway.current["sub_gate15"] = make_subscription()
        row_id = self._ingest(
            db,
            "customer.subscription.updated",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )

        claimed = inbound_service.claim_batch(
            db, worker_id="crashed-worker", batch_size=10, lease_seconds=1
        )
        assert [row.id for row in claimed] == [row_id]

        db.execute(
            update(StripeInboundEvent)
            .where(StripeInboundEvent.id == row_id)
            .values(claim_expires_at=datetime.now(timezone.utc) - timedelta(seconds=5))
        )
        db.flush()

        reaped = inbound_service.reap_expired_leases(db)
        assert reaped == 1

        row = db.get(StripeInboundEvent, row_id)
        db.refresh(row)
        assert row.status is StripeInboundStatus.FAILED
        assert row.claim_expires_at is None

        db.execute(
            update(StripeInboundEvent)
            .where(StripeInboundEvent.id == row_id)
            .values(available_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        db.flush()
        assert inbound_service.claim_batch(db, worker_id="next", batch_size=10)

    def test_attempt_ceiling_dead_letters(self, db, gateway, billing_org):
        row_id = self._ingest(
            db,
            "customer.subscription.updated",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )
        status = inbound_service.mark_failed(
            db, row_id, attempts=8, max_attempts=8, error="repeated failure"
        )
        db.flush()

        assert status is StripeInboundStatus.DEAD
        row = db.get(StripeInboundEvent, row_id)
        db.refresh(row)
        assert row.status is StripeInboundStatus.DEAD
        assert row.processed_at is not None
        assert row.result["dead_reason"] == "attempt_ceiling"

    def test_subscription_missing_at_stripe_does_not_delete_local_history(
        self, db, gateway, billing_org
    ):
        gateway.current["sub_gate15"] = make_subscription(seats=4)
        self._ingest(
            db,
            "customer.subscription.created",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )
        drain(db, gateway)
        assert db.execute(select(Subscription)).scalar_one().seats_purchased == 4

        del gateway.current["sub_gate15"]
        row_id = self._ingest(
            db,
            "customer.subscription.deleted",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )
        drain(db, gateway)

        row = db.get(StripeInboundEvent, row_id)
        assert row.status is StripeInboundStatus.IGNORED
        assert row.result["ignored_reason"] == "subscription_missing_at_stripe"
        assert db.execute(select(Subscription)).scalar_one().seats_purchased == 4

    def test_scheduled_cancellation_reconciles_without_constraint_violation(
        self, db, gateway, billing_org
    ):
        gateway.current["sub_gate15"] = make_subscription(
            status="active",
            cancel_at_period_end=True,
            canceled_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
        )
        row_id = self._ingest(
            db,
            "customer.subscription.updated",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )
        drain(db, gateway)

        assert db.get(StripeInboundEvent, row_id).status is StripeInboundStatus.PROCESSED
        subscription = db.execute(select(Subscription)).scalar_one()
        assert subscription.status.value == "active"
        assert subscription.cancel_at_period_end is True
        assert subscription.canceled_at is not None

    def test_organization_is_backfilled_onto_the_inbound_row(
        self, db, gateway, billing_org
    ):
        gateway.current["sub_gate15"] = make_subscription()
        row_id = self._ingest(
            db,
            "customer.subscription.created",
            {"object": "subscription", "id": "sub_gate15", "customer": "cus_gate15"},
        )
        drain(db, gateway)

        row = db.get(StripeInboundEvent, row_id)
        db.refresh(row)
        assert row.organization_id == billing_org["organization"].id