"""ARCH-50 RevOps — a self-serve checkout priced from the plan price books. ARCH50-S1:revops-checkout

`prepare` resolves (tier, interval, currency) to the published price and its gateway price, refuses a
client-supplied gateway price that belongs to another plan, and reserves a promo code (locked, capped, one per
organization) when one is given -- all inside the request's transaction, so a gateway failure after this rolls the
reservation back with everything else. `record` notes what the checkout sold (checkout_selections), which revenue
metrics read, because the gateway's subscription row does not say whether it was annual or in rupees.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.revops import price_books, promos
from app.services.revops.service import RevOpsError


@dataclass
class PreparedCheckout:
    organization_id: uuid.UUID
    price: price_books.PlanPrice
    seats: int
    price_id: str
    reservation: Optional[dict[str, Any]] = None

    @property
    def discount_code(self) -> Optional[str]:
        return (self.reservation or {}).get("gateway_coupon_id")


def check_price_id(db: Session, *, tier_key: str, price_id: Optional[str]) -> None:
    """A gateway price the platform knows must be sold as the plan it identifies (unknown ids keep the legacy
    path: the gateway itself refuses what it does not sell)."""
    if not price_id:
        return
    known = price_books.tier_for_gateway_price(db, price_id)
    if known is not None and known != tier_key:
        raise RevOpsError(f"gateway price {price_id} sells the {known} plan, not {tier_key}", "PRICE_NOT_SOLD", 400)


def prepare(db: Session, *, organization_id: uuid.UUID, tier_key: str, interval: str, currency: str, seats: int,
            price_id: Optional[str], promo_code: Optional[str]) -> PreparedCheckout:
    price = price_books.resolve_price(db, tier_key=tier_key, interval=interval, currency=currency)
    if price is None or not price.gateway_price_id:
        raise RevOpsError(f"the {tier_key} plan is not sold self-serve billed {interval}ly in {currency}; "
                          "contact sales for an invoiced contract", "PRICE_NOT_SOLD", 400, tier_key=tier_key,
                          interval=interval, currency=currency)
    if price_id and price_id != price.gateway_price_id:
        raise RevOpsError("the price does not match the plan, interval and currency chosen", "PRICE_NOT_SOLD", 400)
    prepared = PreparedCheckout(organization_id, price, int(seats), price.gateway_price_id)
    if promo_code:
        prepared.reservation = promos.reserve(db, organization_id=organization_id, code=promo_code,
                                              tier_key=tier_key, interval=interval, currency=currency,
                                              list_amount_micros=price.unit_amount_micros * int(seats),
                                              for_gateway=True)
    return prepared


def record(db: Session, prepared: PreparedCheckout, *, gateway: str) -> uuid.UUID:
    selection_id = uuid.uuid4()
    db.execute(text(
        "INSERT INTO checkout_selections (id, organization_id, tier_key, billing_interval, currency, book_entry_id, "
        "unit_amount_micros, seats, promo_redemption_id, gateway) VALUES (:id, :o, :t, :i, :c, :e, :a, :s, :r, :g)"),
        {"id": selection_id, "o": prepared.organization_id, "t": prepared.price.tier_key,
         "i": prepared.price.interval, "c": prepared.price.currency, "e": prepared.price.entry_id,
         "a": prepared.price.unit_amount_micros, "s": prepared.seats,
         "r": (prepared.reservation or {}).get("redemption_id"), "g": gateway if gateway in ("STRIPE", "DODO") else "DODO"})
    return selection_id


def tenant_contract(db: Session, *, organization_id: uuid.UUID) -> Optional[dict[str, Any]]:
    from app.services.revops import contracts

    row = db.execute(text("SELECT id FROM enterprise_contracts WHERE organization_id = :o AND status <> 'DRAFT' "
                          "ORDER BY (status = 'ACTIVE') DESC, created_at DESC LIMIT 1"), {"o": organization_id}).first()
    return contracts.detail(db, row[0]) if row else None


__all__ = ["PreparedCheckout", "check_price_id", "prepare", "record", "tenant_contract"]
