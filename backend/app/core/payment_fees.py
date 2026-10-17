"""What Dodo Payments keeps from each payment (website and payments session, C12).

Dodo's published pricing (dodopayments.com/pricing, read 2026-10-16; recorded in
docs/billing/DODO.md): 4% + 40 US cents per payment, plus 1.5% when the card or account is
international, plus 0.5% on subscription payments. As merchant of record Dodo also collects and
remits the tax, which is added on top of the price and is not a cost to Servabo.

Servabo's subscriptions are international by default (an Indian sole proprietor selling to
customers mostly abroad), so the planning figure is the worst usual case: 6% + 40 cents.
Chargebacks cost a further $30 each (`DISPUTE_FEE_MICROS`).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

BASE_RATE = Decimal("0.04")
FIXED_FEE_MICROS = 400_000  # 40 US cents
INTERNATIONAL_RATE = Decimal("0.015")
SUBSCRIPTION_RATE = Decimal("0.005")
DISPUTE_FEE_MICROS = 30_000_000  # $30 per chargeback


def dodo_fee_micros(amount_micros: int, *, international: bool = True, subscription: bool = True) -> int:
    """Dodo's fee on one payment of `amount_micros` (USD), in micros."""
    if amount_micros <= 0:
        return 0
    rate = BASE_RATE + (INTERNATIONAL_RATE if international else 0) + (SUBSCRIPTION_RATE if subscription else 0)
    variable = (Decimal(amount_micros) * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(variable) + FIXED_FEE_MICROS


__all__ = ["BASE_RATE", "DISPUTE_FEE_MICROS", "FIXED_FEE_MICROS", "INTERNATIONAL_RATE", "SUBSCRIPTION_RATE",
           "dodo_fee_micros"]
