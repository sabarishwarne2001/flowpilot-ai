"""A Dodo payment, recorded as a paid invoice (website and payments session, C4).

Under Stripe, Servabo's invoices are its own period statements, assembled when a renewal closes a
period. Under Dodo, a merchant of record, the legal invoice is Dodo's: Dodo sells to the customer
and issues the tax invoice for every payment. Nothing recorded those payments, so a Dodo customer's
Billing → Invoices page was empty.

Each `payment.succeeded` now becomes one invoice row, gateway-neutral:

* the amounts are Dodo's, re-fetched (`GET /payments/{id}`, D-9): `total_amount` includes tax, so
  the subtotal is the total less `tax`;
* `gateway = 'DODO'`, `gateway_invoice_id` = Dodo's payment id (unique per gateway, so a replayed or
  repeated event records nothing new);
* the period is the subscription's when the payment opened it (checkout, renewal, a held renewal
  paid), or runs from the payment to the period's end for a charge inside a period (a plan change);
* it is sealed (finalized, with its content digest) and PAID, and its notes say who issued the tax
  invoice, which `GET .../invoices/{id}/download` streams from Dodo.

An add-on's payment is recorded the same way against no subscription row of Servabo's (add-ons live
on their own ledger), pinned to the organization's plan for its provenance columns.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billing_account import BillingAccount
from app.models.invoice import Invoice, InvoiceLineItem, InvoiceLineKind, InvoiceStatus
from app.models.quota_tier import QuotaTier
from app.models.subscription import Subscription
from app.services.billing import invoice_service
from app.services.billing.dodo_gateway import GATEWAY_NAME, DodoPaymentSnapshot

logger = logging.getLogger("app.services.billing.dodo_invoice")

#: Who issues the tax invoice, shown beside every Dodo invoice.
ISSUED_BY = "Dodo Payments (merchant of record)"

#: A payment this close to its period's start opened that period (Dodo charges in advance).
_OPENS_PERIOD_WITHIN = timedelta(days=1)


def existing(db: Session, *, payment_id: str) -> Optional[Invoice]:
    return db.execute(
        select(Invoice).where(Invoice.gateway == GATEWAY_NAME, Invoice.gateway_invoice_id == payment_id)
    ).scalar_one_or_none()


def _plan_subscription(db: Session, *, organization_id: uuid.UUID) -> Optional[Subscription]:
    """The organization's newest plan subscription (any status): the provenance of an add-on's payment."""
    return db.execute(
        select(Subscription)
        .join(BillingAccount, BillingAccount.id == Subscription.billing_account_id)
        .where(BillingAccount.organization_id == organization_id)
        .order_by(Subscription.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _period_taken(db: Session, *, subscription: Subscription, start: datetime) -> bool:
    return db.execute(
        select(Invoice.id).where(
            Invoice.subscription_id == subscription.id,
            Invoice.period_start == start,
            Invoice.status != InvoiceStatus.VOID,
        )
    ).first() is not None


def record_payment(
    db: Session,
    *,
    organization_id: uuid.UUID,
    payment: DodoPaymentSnapshot,
    addon_name: Optional[str] = None,
) -> Optional[Invoice]:
    """Record one succeeded Dodo payment as a paid invoice; the existing row when already recorded."""
    found = existing(db, payment_id=payment.id)
    if found is not None:
        return found
    if payment.status != "succeeded" or payment.total_micros <= 0:
        return None

    account = db.execute(
        select(BillingAccount).where(BillingAccount.organization_id == organization_id)
    ).scalar_one_or_none()
    if account is None:
        logger.warning("dodo.payment_without_account", extra={"payment_id": payment.id})
        return None

    plan: Optional[Subscription] = None
    if payment.subscription_id and addon_name is None:
        plan = db.execute(
            select(Subscription).where(
                Subscription.gateway == GATEWAY_NAME,
                Subscription.gateway_subscription_id == payment.subscription_id,
            )
        ).scalar_one_or_none()
    pins = plan or _plan_subscription(db, organization_id=organization_id)
    if pins is None:
        logger.warning("dodo.payment_without_plan", extra={"payment_id": payment.id})
        return None

    paid_at = payment.created_at
    reason = "subscription"
    if plan is not None:
        start, end = plan.current_period_start, plan.current_period_end
        opens = abs((paid_at - start).total_seconds()) <= _OPENS_PERIOD_WITHIN.total_seconds()
        if not opens or _period_taken(db, subscription=plan, start=start):
            # A charge inside a period (a plan change), or a payment for a period already invoiced.
            reason = "plan_change"
            start = paid_at
            end = plan.current_period_end if plan.current_period_end > paid_at else paid_at + timedelta(seconds=1)
    else:
        start, end = paid_at, paid_at + timedelta(days=30)
        reason = "addon"

    tier = db.get(QuotaTier, pins.quota_tier_id)
    seats = int(pins.seats_purchased) if plan is not None else 1
    subtotal = int(payment.total_micros) - int(payment.tax_micros)
    when = f"{start:%d %b %Y} to {end:%d %b %Y}"
    if reason == "addon":
        description = f"{addon_name} add-on, {when}"
    elif reason == "plan_change":
        description = f"Plan change: {tier.display_name if tier else pins.quota_tier_key} plan, " \
                      f"{seats} seat{'s' if seats != 1 else ''}, {when}"
    else:
        description = f"{tier.display_name if tier else pins.quota_tier_key} plan, " \
                      f"{seats} seat{'s' if seats != 1 else ''}, {when}"

    invoice = Invoice(
        billing_account_id=account.id,
        subscription_id=plan.id if plan is not None else None,
        gateway=GATEWAY_NAME,
        gateway_invoice_id=payment.id,
        number=invoice_service.allocate_number(db, period_start=start),
        status=InvoiceStatus.DRAFT,
        currency=payment.currency,
        period_start=start,
        period_end=end,
        subtotal_micros=subtotal,
        tax_micros=int(payment.tax_micros),
        total_micros=int(payment.total_micros),
        amount_paid_micros=0,
        price_book_id=pins.price_book_id,
        quota_tier_id=pins.quota_tier_id,
        seats_billed=seats,
        # Sealed below; the column's shape check needs a digest-shaped value until then.
        content_digest="sha256:" + "0" * 64,
        assembly_notes={"issued_by": ISSUED_BY, "dodo_payment_id": payment.id, "charge": reason},
    )
    db.add(invoice)
    db.flush([invoice])
    line = InvoiceLineItem(
        invoice_id=invoice.id,
        line_number=1,
        kind=InvoiceLineKind.SEAT,
        description=description,
        quantity=Decimal(1),
        unit="payment",
        unit_price_micros=Decimal(subtotal),
        amount_micros=subtotal,
    )
    db.add(line)
    db.flush([line])
    invoice_service.finalize_invoice(db, invoice=invoice, lines=[line])
    invoice_service.record_payment(db, invoice=invoice, amount_paid_micros=int(payment.total_micros),
                                   paid_at=paid_at)
    if payment.refunded_micros:
        invoice_service.record_refund(db, invoice=invoice, amount_refunded_micros=payment.refunded_micros)
    logger.info("dodo.payment_recorded",
                extra={"number": invoice.number, "payment_id": payment.id, "charge": reason})
    return invoice


__all__ = ["ISSUED_BY", "existing", "record_payment"]
