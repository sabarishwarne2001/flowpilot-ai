"""The platform margin console counts Dodo Payments' fees (website and payments session, C12).

The margin console set usage revenue against what serving it cost, and nothing set the money
received against what the payment provider kept. The summary now reports, for the window, the
payments recorded (Dodo invoices paid in it), Dodo's fee on each (6% + 40 cents for an
international subscription, `app.core.payment_fees`) and the refunds given back.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.payment_fees import dodo_fee_micros
from app.services import margin_service
from tests.services.test_dodo_invoices import invoices
from tests.services.test_dodo_reconciliation import BUSINESS_PRICE, dodo, org, subscribe  # noqa: F401


def test_the_summary_reports_payments_and_dodos_fees(dodo, client, db, org) -> None:
    subscribe(dodo, client, db, org, seats=2)
    [invoice] = invoices(db, org["organization"].id)
    now = datetime.now(timezone.utc)
    summary = margin_service.platform_summary(db, period_start=now - timedelta(days=1), period_end=now + timedelta(days=1))
    assert summary.payments.count == 1
    assert summary.payments.received_micros == invoice.total_micros == BUSINESS_PRICE * 2 * 10_000
    assert summary.payments.fees_micros == dodo_fee_micros(invoice.total_micros)
    assert summary.payments.refunded_micros == 0
