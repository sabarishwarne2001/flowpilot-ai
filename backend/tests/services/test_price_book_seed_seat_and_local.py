"""The production price book seed prices a seat per plan (N-030) and the local model (N-031).

N-030: the seed wrote no `billing.seat` entry, so every seat disclosure (Billing ->
Seats, adding members past the purchased seats) said "unpriced" and the API logged
`ERROR billing.seat_disclosure_unpriced` on each one; the invoice's seat line fell
back the same way. The seat lookup also ignored the plan, so per-plan entries
would all have resolved to whichever sorted first.

N-031: the sovereign edition's local model had no price, so every model call logged
`CRITICAL llm.settle_price_unavailable` and its cost was UNKNOWN. A self-hosted model
runs on the customer's own hardware: the platform buys nothing per token, so it is
priced at zero with a declared zero cost basis (never an undeclared zero).
"""

from __future__ import annotations

import importlib.util
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billing_account import BillingAccount
from app.models.price_book import PriceBook
from app.models.subscription import Subscription, SubscriptionStatus
from app.services import pricing_service
from app.services.billing import invoice_service, seat_service, stripe_gateway
from tests.security.plans import put_on_plan

_SEED = Path(__file__).resolve().parents[2] / "scripts" / "seed_price_book.py"

PLAN_SEAT_PRICES = {
    "developer": 49_000_000,
    "business": 299_000_000,
    "enterprise": 799_000_000,
}


def _seed_module():
    spec = importlib.util.spec_from_file_location("seed_price_book_n030", _SEED)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _publish_seed(db: Session, *, version: int = 1, entries=None) -> PriceBook:
    seed = _seed_module()
    book = pricing_service.publish(
        db,
        version=version,
        effective_from=datetime.now(timezone.utc) - timedelta(days=2 if version == 1 else 1),
        entries=entries if entries is not None else seed._load_entries(None),  # noqa: SLF001
        currency="USD",
        notes="test",
    )
    db.flush()
    pricing_service.clear_cache()
    return book


def test_the_seed_prices_one_seat_on_each_paid_plan() -> None:
    entries = _seed_module()._load_entries(None)  # noqa: SLF001
    seats = {e.tier_key: e for e in entries if e.event_type == "billing.seat"}
    assert {key: int(e.unit_price_micros) for key, e in seats.items()} == PLAN_SEAT_PRICES
    assert all(e.unit == "seat" for e in seats.values())


def test_the_seed_publishes_and_the_seat_lookup_reads_the_plan(db_session: Session) -> None:
    book = _publish_seed(db_session)
    for plan, price in PLAN_SEAT_PRICES.items():
        entry = invoice_service.seat_price_entry(db_session, price_book_id=book.id, tier_key=plan)
        assert entry is not None, plan
        assert int(entry.unit_price_micros) == price, plan
    # A plan the book does not price (and no plan at all) is unpriced, never another plan's price.
    assert invoice_service.seat_price_entry(db_session, price_book_id=book.id, tier_key="free") is None
    assert invoice_service.seat_price_entry(db_session, price_book_id=book.id) is None


def test_a_seat_disclosure_on_the_business_plan_is_priced(
    db_session: Session, tenant, caplog: pytest.LogCaptureFixture
) -> None:
    tier = put_on_plan(db_session, tenant.organization, "business")
    book = db_session.execute(select(PriceBook).order_by(PriceBook.version.desc())).scalars().first()
    customer = f"cus_test_{uuid.uuid4().hex[:10]}"
    account = BillingAccount(
        organization_id=tenant.organization.id,
        stripe_customer_id=customer,
        gateway_customer_id=customer,
        currency="USD",
        billing_email="billing@acme.example",
    )
    db_session.add(account)
    db_session.flush()
    now = datetime.now(timezone.utc)
    reference = f"sub_test_{uuid.uuid4().hex[:10]}"
    db_session.add(
        Subscription(
            billing_account_id=account.id,
            gateway="STRIPE",
            stripe_subscription_id=reference,
            gateway_subscription_id=reference,
            status=SubscriptionStatus.ACTIVE,
            quota_tier_key=tier.key,
            quota_tier_id=tier.id,
            price_book_id=book.id,
            seats_purchased=3,
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=29),
        )
    )
    db_session.commit()

    class _NoPreview:
        def preview_seat_change(self, **_: object):
            raise RuntimeError("no preview in this test")

    stripe_gateway.set_gateway(_NoPreview())
    try:
        with caplog.at_level(logging.ERROR, logger="app.services.billing.seat"):
            disclosure = seat_service.seat_price_disclosure(
                db_session, organization_id=tenant.organization.id
            )
    finally:
        stripe_gateway.reset_gateway()

    assert disclosure.price_source == seat_service.PRICE_SOURCE_BOOK
    assert disclosure.unit_price_micros == PLAN_SEAT_PRICES["business"]
    assert not [r for r in caplog.records if r.getMessage() == "billing.seat_disclosure_unpriced"]


@pytest.mark.parametrize("event_type", ["llm.input_token", "llm.output_token"])
def test_the_local_model_is_priced_at_a_declared_zero(db_session: Session, event_type: str) -> None:
    _publish_seed(db_session)
    price = pricing_service.resolve(
        db_session, event_type=event_type, provider="local", model="flowpilot-e2e-local"
    )
    assert price.unit_price_micros == Decimal("0")
    assert price.cost_basis_micros == Decimal("0")
    assert price.cost_basis_source == "ZERO_BYOK"


def test_auto_version_publishes_only_when_the_seed_changed(db_session: Session) -> None:
    seed = _seed_module()
    entries = seed._load_entries(None)  # noqa: SLF001
    # A server seeded before this release: v1 without the seat and local entries.
    old = [e for e in entries if e.event_type != "billing.seat" and e.provider != "local"]
    _publish_seed(db_session, entries=old)
    db_session.commit()

    assert seed.next_version_for(db_session, entries) == 2
    _publish_seed(db_session, version=2, entries=entries)
    db_session.commit()
    assert seed.next_version_for(db_session, entries) is None, "an unchanged seed published again"
