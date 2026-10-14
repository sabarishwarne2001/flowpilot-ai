"""A refund made at the payment gateway is recorded against the invoice (campaign session 4, N-069).

Before: a Stripe `charge.refunded` event was filed as "credit note not modelled" and ignored, so a
refunded invoice still read "Paid" in full to the customer, nobody at the organization was told,
and the RevOps console had no figure for money given back. Now the gateway's charge is re-fetched
(the gateway is the authority, as for invoices), the invoice records how much of its payment was
refunded and when, the owners and Billing members are told, and RevOps shows refunds per currency.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import select

from app.models.invoice import InvoiceStatus
from app.models.notification import Notification
from app.services.billing import invoice_service, reconcile_service
from app.services.billing.stripe_gateway import StripeEvent

from tests.services.test_arch15_gate_15_1_15_2_inbound import (  # noqa: F401
    FakeStripeGateway,
    billing_org,
    gateway,
    stripe_settings,
)
from tests.services.test_arch15_gate_15_5_15_6_invoices import assemble_for, priced_org  # noqa: F401


def _charge(gateway_fake: FakeStripeGateway, charge_id: str, **fields: Any) -> None:
    gateway_fake.charges[charge_id] = {"id": charge_id, "object": "charge", "customer": "cus_gate15", **fields}


def _event(event_type: str, obj: dict[str, Any]) -> StripeEvent:
    return StripeEvent(
        id=f"evt_{uuid.uuid4().hex[:12]}",
        type=event_type,
        created=datetime.now(timezone.utc),
        livemode=False,
        api_version="2026-07-29.dahlia",
        payload={"data": {"object": obj}},
    )


@pytest.fixture()
def paid_invoice(db, gateway, priced_org):  # noqa: F811
    invoice = assemble_for(db, priced_org, gateway, seats=1).invoice
    invoice_service.record_payment(db, invoice=invoice, amount_paid_micros=invoice.total_micros)
    invoice.stripe_invoice_id = f"in_{uuid.uuid4().hex[:10]}"
    db.flush()
    assert invoice.total_micros > 0
    return invoice


def _cents(micros: int) -> int:
    return micros // invoice_service.MICROS_PER_CENT


def test_a_partial_refund_is_recorded_and_announced(db, gateway, priced_org, paid_invoice):  # noqa: F811
    half = _cents(paid_invoice.total_micros) // 2
    _charge(gateway, "ch_partial", invoice=paid_invoice.stripe_invoice_id, currency="usd",
            amount=_cents(paid_invoice.total_micros), amount_refunded=half, refunded=False)

    outcome = reconcile_service.reconcile_event(db, _event("charge.refunded", {"id": "ch_partial", "object": "charge"}))
    assert outcome.handled, outcome
    db.refresh(paid_invoice)

    assert getattr(paid_invoice, "amount_refunded_micros", None) == half * invoice_service.MICROS_PER_CENT
    assert paid_invoice.refunded_at is not None
    # The payment happened; the invoice stays paid, with the refund recorded beside it (as Stripe shows it).
    assert paid_invoice.status is InvoiceStatus.PAID

    notices = db.execute(select(Notification).where(
        Notification.organization_id == priced_org["organization"].id)).scalars().all()
    assert any("refund" in n.title.lower() for n in notices), [n.title for n in notices]


def test_a_full_refund_and_a_refund_that_later_fails(db, gateway, priced_org, paid_invoice):  # noqa: F811
    total = _cents(paid_invoice.total_micros)
    _charge(gateway, "ch_full", invoice=paid_invoice.stripe_invoice_id, currency="usd",
            amount=total, amount_refunded=total, refunded=True)
    assert reconcile_service.reconcile_event(db, _event("charge.refunded", {"id": "ch_full"})).handled
    db.refresh(paid_invoice)
    assert paid_invoice.amount_refunded_micros == paid_invoice.amount_paid_micros

    # The same event delivered again changes nothing.
    assert reconcile_service.reconcile_event(db, _event("charge.refunded", {"id": "ch_full"})).handled
    db.refresh(paid_invoice)
    assert paid_invoice.amount_refunded_micros == paid_invoice.amount_paid_micros

    # The bank returned the refund (`charge.refund.updated`, status failed): the charge's refunded
    # amount goes back down, and so does ours.
    gateway.charges["ch_full"]["amount_refunded"] = 0
    gateway.charges["ch_full"]["refunded"] = False
    gateway.refunds["re_1"] = {"id": "re_1", "object": "refund", "charge": "ch_full", "status": "failed"}
    assert reconcile_service.reconcile_event(db, _event("charge.refund.updated", {"id": "re_1"})).handled
    db.refresh(paid_invoice)
    assert paid_invoice.amount_refunded_micros == 0


def test_a_refund_for_a_charge_with_no_invoice_of_ours_is_ignored(db, gateway, paid_invoice):  # noqa: F811
    _charge(gateway, "ch_other", invoice="in_not_ours", currency="usd", amount=500, amount_refunded=500)
    outcome = reconcile_service.reconcile_event(db, _event("charge.refunded", {"id": "ch_other"}))
    assert not outcome.handled
    db.refresh(paid_invoice)
    assert paid_invoice.amount_refunded_micros == 0


def test_revops_shows_money_refunded_per_currency(db, gateway, priced_org, paid_invoice):  # noqa: F811
    from app.services.revops import metrics

    total = _cents(paid_invoice.total_micros)
    _charge(gateway, "ch_rev", invoice=paid_invoice.stripe_invoice_id, currency="usd",
            amount=total, amount_refunded=total, refunded=True)
    assert reconcile_service.reconcile_event(db, _event("charge.refunded", {"id": "ch_rev"})).handled
    db.flush()

    report = metrics.compute(db)
    refunds = report.get("refunds", {}).get(paid_invoice.currency)
    assert refunds is not None, report.get("refunds")
    assert refunds["refunded_micros"] >= paid_invoice.amount_paid_micros
    assert refunds["invoices"] >= 1
