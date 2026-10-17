"""Overage is charged when a billing period closes (campaign session 4, F-339, N-071).

    pytest tests/services/test_overage_is_billed_at_period_close.py -q

Before: the plans "bill overage" (OCR pages, storage, tokens past the allowance), but nothing ever
assembled a period's statement. Stripe's renewal (`invoice.created`) was ignored as "not yet
implemented", its payment answered "assembly_pending" forever, the in-app invoice list stayed
empty, refunds and failed payments found no invoice, and no overage line ever reached Stripe. The
statement also counted one seat's allowance, so once it ran, a multi-seat organization would have
been charged for usage its plan includes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.models.invoice import Invoice, InvoiceLineKind, InvoiceStatus
from app.services.billing import invoice_service, reconcile_service
from app.services.billing.stripe_gateway import StripeEvent

from tests.services.test_arch15_gate_15_1_15_2_inbound import (  # noqa: F401
    FakeStripeGateway,
    billing_org,
    gateway,
    stripe_settings,
)
from tests.services.test_arch15_gate_15_3_15_4_subscriptions_seats import install_subscription
from tests.services.test_arch15_gate_15_5_15_6_invoices import PERIOD_END, PERIOD_START, add_usage

SEAT_CENTS = 2_500  # the fixture's book: $25 a seat
OVERAGE_CENTS = 100  # 500,000 tokens past two seats' 2,000,000, at 2 micros a token


def _renewal(fake, invoice_id: str, *, status: str = "draft", reason: str = "subscription_cycle",
             total: int = 2 * SEAT_CENTS + OVERAGE_CENTS, paid: int = 0) -> None:
    fake.invoices[invoice_id] = {
        "status": status, "billing_reason": reason, "subscription": "sub_gate15", "customer": "cus_gate15",
        "currency": "USD", "period_start": int(PERIOD_START.timestamp()), "period_end": int(PERIOD_END.timestamp()),
        "total": total, "subtotal": total, "amount_paid": paid, "paid": paid >= total and paid > 0,
    }


def _event(kind: str, invoice_id: str, n: int = 1) -> StripeEvent:
    return StripeEvent(id=f"evt_{kind}_{invoice_id}_{n}", type=kind, created=datetime.now(timezone.utc),
                       livemode=False, api_version=None,
                       payload={"data": {"object": {"id": invoice_id, "object": "invoice"}}})


def _statement(db, stripe_invoice_id: str) -> Invoice | None:
    return invoice_service.get_by_stripe_id(db, stripe_invoice_id=stripe_invoice_id)


def _two_seats_over_their_allowance(db, gateway, billing_org) -> None:  # noqa: F811
    install_subscription(db, billing_org, gateway, seats=2)
    add_usage(db, billing_org["organization"].id, quantity=2_500_000)


def test_the_renewal_closes_the_period_and_carries_its_overage(db, gateway, billing_org) -> None:  # noqa: F811
    _two_seats_over_their_allowance(db, gateway, billing_org)
    _renewal(gateway, "in_renewal")

    outcome = reconcile_service.reconcile_event(db, _event("invoice.created", "in_renewal"))

    assert outcome.handled, outcome
    statement = _statement(db, "in_renewal")
    assert statement is not None and statement.status is InvoiceStatus.OPEN
    lines = {line.kind: line for line in invoice_service._lines_of(db, statement)}  # noqa: SLF001
    assert lines[InvoiceLineKind.INCLUDED].included_quantity == Decimal("2000000"), "per seat, pooled (N-044)"
    assert lines[InvoiceLineKind.OVERAGE].quantity == Decimal("500000")
    [item] = gateway.invoice_items
    assert item["invoice"] == "in_renewal" and item["amount"] == OVERAGE_CENTS and item["currency"] == "USD"
    assert item["metadata"]["servabo_invoice"] == statement.number
    assert statement.assembly_notes["overage_billing"]["status"] == "ON_INVOICE"

    # The same event again (a webhook retry) charges nothing twice.
    again = reconcile_service.reconcile_event(db, _event("invoice.created", "in_renewal", 2))
    assert again.handled and len(gateway.invoice_items) == 1
    assert db.query(Invoice).filter(Invoice.subscription_id == statement.subscription_id).count() == 1

    # The renewal is paid: the statement says so.
    _renewal(gateway, "in_renewal", status="paid", paid=2 * SEAT_CENTS + OVERAGE_CENTS)
    paid = reconcile_service.reconcile_event(db, _event("invoice.paid", "in_renewal"))
    assert paid.handled and paid.detail.get("totals_agree") is True, paid
    db.refresh(statement)
    assert statement.status is InvoiceStatus.PAID


def test_when_the_renewal_was_already_finalized_the_overage_goes_on_the_next_invoice(
    db, gateway, billing_org  # noqa: F811
) -> None:
    _two_seats_over_their_allowance(db, gateway, billing_org)
    # `invoice.created` was lost; the first word of the renewal is its payment.
    _renewal(gateway, "in_late", status="paid", paid=2 * SEAT_CENTS)

    outcome = reconcile_service.reconcile_event(db, _event("invoice.paid", "in_late"))

    assert outcome.handled, outcome
    statement = _statement(db, "in_late")
    assert statement is not None
    [item] = gateway.invoice_items
    assert item["invoice"] is None and item["subscription"] == "sub_gate15" and item["amount"] == OVERAGE_CENTS
    assert statement.assembly_notes["overage_billing"]["status"] == "NEXT_INVOICE"


def test_a_first_invoice_or_a_seat_change_closes_no_period(db, gateway, billing_org) -> None:  # noqa: F811
    _two_seats_over_their_allowance(db, gateway, billing_org)
    for invoice_id, reason in (("in_first", "subscription_create"), ("in_proration", "subscription_update")):
        _renewal(gateway, invoice_id, reason=reason)
        created = reconcile_service.reconcile_event(db, _event("invoice.created", invoice_id))
        assert created.handled is False and created.ignored_reason == "closes_no_period", created
        _renewal(gateway, invoice_id, reason=reason, status="paid", paid=2 * SEAT_CENTS)
        paid = reconcile_service.reconcile_event(db, _event("invoice.paid", invoice_id))
        assert paid.handled and paid.detail["action"] == "no_statement", paid
        assert _statement(db, invoice_id) is None
    assert gateway.invoice_items == []


def test_a_statement_includes_every_seats_allowance(db, gateway, billing_org) -> None:  # noqa: F811
    subscription = install_subscription(db, billing_org, gateway, seats=3)
    add_usage(db, billing_org["organization"].id, quantity=2_500_000)
    subscription.current_period_start, subscription.current_period_end = PERIOD_START, PERIOD_END
    db.flush()

    result = invoice_service.assemble(db, subscription=subscription)

    kinds = [line.kind for line in result.lines]
    assert InvoiceLineKind.OVERAGE not in kinds, "three seats include 3,000,000; 2,500,000 is within it"
    included = next(line for line in result.lines if line.kind is InvoiceLineKind.INCLUDED)
    assert included.included_quantity == Decimal("3000000")
