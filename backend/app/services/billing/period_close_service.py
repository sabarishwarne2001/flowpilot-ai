"""Campaign session 4 (F-339, N-071) — closing a billing period: the statement and its overage.

WHAT HAPPENS AT THE END OF A PERIOD
===================================

Stripe bills the seats of a per-seat plan IN ADVANCE: at each period boundary it opens the
renewal invoice (`invoice.created`, `billing_reason = subscription_cycle`) for the next period's
seats, keeps it as a draft for about an hour, then finalizes and charges it. Usage beyond the
plan's allowances is billed IN ARREARS, so the boundary is also when the period that just closed
can be counted.

`close_period` does both halves of that, once per period:

1. assembles Servabo's statement of the closed period (`invoice_service.assemble`: the seats,
   what the plan included, per seat and pooled, and the overage priced from the pinned book),
   linked to the renewal invoice that carries its overage;
2. adds each overage line to that renewal invoice while it is still a draft, so the customer is
   charged for it with the next period's seats. When the draft has already been finalized (the
   webhook arrived late), the lines go on the subscription's next invoice instead, and the
   statement says so.

Before this, nothing assembled a statement: `billing.assemble_invoice` was never queued, the
in-app invoice list stayed empty, payments and refunds found no invoice to update, and overage the
plans promise to bill was never charged.

Only the Stripe gateway can take usage lines today (`supports_usage_billing` is False for Dodo,
pending its verification); on another gateway the statement is still assembled and records that
its overage was not billed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.billing_account import BillingAccount
from app.models.invoice import InvoiceLineKind
from app.models.subscription import Subscription
from app.services.billing import invoice_service, stripe_gateway

logger = logging.getLogger("app.services.billing.period_close")

ON_INVOICE = "ON_INVOICE"
NEXT_INVOICE = "NEXT_INVOICE"
NOT_SUPPORTED = "NOT_SUPPORTED"


def _cents(micros: int) -> int:
    return int((Decimal(int(micros)) / Decimal(invoice_service.MICROS_PER_CENT)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))


def _overage_items(subscription: Subscription, result: invoice_service.AssemblyResult,
                   ) -> list[stripe_gateway.StripeInvoiceItemDraft]:
    invoice = result.invoice
    period = (f"{invoice_service._as_utc(invoice.period_start):%d %b %Y} – "  # noqa: SLF001
              f"{invoice_service._as_utc(invoice.period_end):%d %b %Y}")  # noqa: SLF001
    items = []
    for line in result.lines:
        if line.kind is not InvoiceLineKind.OVERAGE or _cents(line.amount_micros) <= 0:
            continue
        items.append(stripe_gateway.StripeInvoiceItemDraft(
            # Stable across retries of the same period: a re-run never charges a line twice.
            key=f"overage:{subscription.id}:{invoice.period_start.isoformat()}:{line.limit_key}",
            amount_cents=_cents(line.amount_micros),
            description=f"{line.description} ({period})",
            metadata={"servabo_invoice": invoice.number, "servabo_line": str(line.line_number),
                      "limit_key": str(line.limit_key or "")},
        ))
    return items


def close_period(
    db: Session,
    *,
    subscription: Subscription,
    period_start: datetime,
    period_end: datetime,
    gateway_invoice_id: Optional[str],
    gateway_invoice_is_draft: bool,
) -> invoice_service.AssemblyResult:
    """The closed period's statement, its overage billed. Idempotent per period."""
    result = invoice_service.assemble(db, subscription=subscription, period_start=period_start,
                                      period_end=period_end, finalize=False,
                                      stripe_invoice_id=gateway_invoice_id)
    invoice = result.invoice
    if invoice.finalized_at is not None:
        return result  # closed before: its overage went out with it

    notes: dict[str, Any] = dict(invoice.assembly_notes or {})
    items = _overage_items(subscription, result)
    if items:
        billing: dict[str, Any] = {"amount_cents": sum(i.amount_cents for i in items), "lines": len(items)}
        if subscription.gateway != "STRIPE" or not subscription.stripe_subscription_id:
            billing["status"] = NOT_SUPPORTED
            logger.error("billing.overage_not_billable",
                         extra={"subscription_id": str(subscription.id), "gateway": subscription.gateway,
                                "amount_cents": billing["amount_cents"]})
        else:
            account = db.get(BillingAccount, invoice.billing_account_id)
            gateway = stripe_gateway.get_gateway()
            target = gateway_invoice_id if gateway_invoice_is_draft else None
            try:
                ids = gateway.add_invoice_items(customer_id=account.stripe_customer_id, currency=invoice.currency,
                                                items=items, invoice_id=target,
                                                subscription_id=subscription.stripe_subscription_id)
            except stripe_gateway.StripePermanentError:
                if target is None:
                    raise
                # The draft was finalized between the fetch and the call: the next invoice takes it.
                target = None
                ids = gateway.add_invoice_items(customer_id=account.stripe_customer_id, currency=invoice.currency,
                                                items=items, invoice_id=None,
                                                subscription_id=subscription.stripe_subscription_id)
            billing.update(status=ON_INVOICE if target else NEXT_INVOICE, gateway_invoice_id=target,
                           item_ids=ids)
            logger.info("billing.overage_billed", extra={"invoice": invoice.number, **billing})
        notes["overage_billing"] = billing
    invoice.assembly_notes = notes or None
    invoice_service.finalize_invoice(db, invoice=invoice, lines=result.lines)
    return result


def close_for_gateway_invoice(
    db: Session, *, snapshot: stripe_gateway.StripeInvoiceSnapshot
) -> Optional[invoice_service.AssemblyResult]:
    """Close the period a Stripe renewal invoice ends, or None when the invoice closes no period.

    Only a renewal (`subscription_cycle`) closes one: a first invoice opens the first period, a
    seat change's proration invoice closes nothing, and neither has usage to bill.
    """
    from app.services.billing import subscription_service

    if str((snapshot.raw or {}).get("billing_reason") or "") != "subscription_cycle":
        return None
    if not snapshot.subscription_id or snapshot.period_start is None or snapshot.period_end is None:
        return None
    if snapshot.period_end <= snapshot.period_start:
        return None
    subscription = subscription_service.get_by_stripe_id(db, stripe_subscription_id=snapshot.subscription_id)
    if subscription is None:
        return None
    return close_period(db, subscription=subscription, period_start=snapshot.period_start,
                        period_end=snapshot.period_end, gateway_invoice_id=snapshot.id,
                        gateway_invoice_is_draft=snapshot.status == "draft")


__all__ = ["NEXT_INVOICE", "NOT_SUPPORTED", "ON_INVOICE", "close_for_gateway_invoice", "close_period"]
