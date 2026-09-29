"""ARCH-50 RevOps — plan price books: annual intervals and INR alongside USD. ARCH50-S1:price-books

A book is drafted, filled with (tier, interval) -> amount and gateway price entries, then PUBLISHED: from then on
the database refuses any change to the book or its entries (trigger `plan_price_books_guard` /
`plan_price_book_entries_guard`), its digest is stored, and the book it replaces in the same currency is RETIRED
in the same transaction (a partial UNIQUE allows one published book per currency). A gateway price identifies
exactly one (tier, interval, currency) -- across books and across the tier versions' own prices -- enforced by
`plan_price_book_entries_gateway_single_plan`, so a webhook's product id can never be read as two plans.

The legacy single price on a tier version (ARCH-29) still sells: `resolve_price` falls back to it when a
published book has no entry for the requested (tier, interval, currency).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.revops import vocabulary as v
from app.services.revops.service import RevOpsError, now


@dataclass(frozen=True)
class PlanPrice:
    tier_key: str
    interval: str
    currency: str
    unit_amount_micros: int
    gateway_price_id: Optional[str]
    entry_id: Optional[uuid.UUID]
    book_code: Optional[str]
    source: str  # BOOK | TIER

    @property
    def unit_amount_minor(self) -> int:
        return self.unit_amount_micros // v.MICROS_PER_MINOR_UNIT

    def as_dict(self) -> dict[str, Any]:
        return {"tier_key": self.tier_key, "interval": self.interval, "currency": self.currency,
                "unit_amount": self.unit_amount_minor, "unit_amount_micros": self.unit_amount_micros,
                "price_id": self.gateway_price_id, "entry_id": str(self.entry_id) if self.entry_id else None,
                "book_code": self.book_code, "source": self.source}


def _book(db: Session, book_id: uuid.UUID, *, lock: bool = False) -> Any:
    row = db.execute(text("SELECT id, code, currency, status, notes, content_digest, published_at, retired_at, "
                          "created_at FROM plan_price_books WHERE id = :id" + (" FOR UPDATE" if lock else "")),
                     {"id": book_id}).mappings().first()
    if row is None:
        raise RevOpsError("no such price book", "NOT_FOUND", 404)
    return row


def book_detail(db: Session, book_id: uuid.UUID) -> dict[str, Any]:
    book = dict(_book(db, book_id))
    book["entries"] = [dict(r) for r in db.execute(text(
        "SELECT id, tier_key, billing_interval, unit_amount_micros, gateway_price_id FROM plan_price_book_entries "
        "WHERE book_id = :id ORDER BY tier_key, billing_interval"), {"id": book_id}).mappings().all()]
    return book


def list_books(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(text(
        "SELECT b.id, b.code, b.currency, b.status, b.notes, b.content_digest, b.published_at, b.retired_at, "
        "b.created_at, count(e.id) AS entries FROM plan_price_books b LEFT JOIN plan_price_book_entries e "
        "ON e.book_id = b.id GROUP BY b.id ORDER BY b.currency, b.created_at DESC")).mappings().all()
    return [dict(r) for r in rows]


def create_book(db: Session, *, code: str, currency: str, notes: Optional[str],
                actor_id: Optional[uuid.UUID]) -> dict[str, Any]:
    code = str(code or "").strip().upper()
    if currency not in v.CURRENCIES:
        raise RevOpsError(f"{currency} is not a supported currency", "PRICE_NOT_SOLD", 422, currency=currency)
    if db.execute(text("SELECT 1 FROM plan_price_books WHERE code = :c"), {"c": code}).first():
        raise RevOpsError(f"a price book named {code} exists", "BOOK_CODE_TAKEN")
    book_id = uuid.uuid4()
    db.execute(text("INSERT INTO plan_price_books (id, code, currency, notes, created_by_user_id) "
                    "VALUES (:id, :c, :cur, :n, :u)"),
               {"id": book_id, "c": code, "cur": currency, "n": (notes or None), "u": actor_id})
    db.flush()
    return book_detail(db, book_id)


def set_entry(db: Session, *, book_id: uuid.UUID, tier_key: str, interval: str, unit_amount_minor: int,
              gateway_price_id: Optional[str]) -> dict[str, Any]:
    book = _book(db, book_id, lock=True)
    if book["status"] != "DRAFT":
        raise RevOpsError(f"price book {book['code']} is {book['status']}", "BOOK_NOT_DRAFT")
    if interval not in v.PLAN_INTERVALS:
        raise RevOpsError(f"{interval} is not a plan interval", "PRICE_NOT_SOLD", 422)
    if int(unit_amount_minor) < 0:
        raise RevOpsError("an amount cannot be negative", "PRICE_NOT_SOLD", 422)
    _require_tier(db, tier_key)
    db.execute(text(
        "INSERT INTO plan_price_book_entries (id, book_id, tier_key, billing_interval, unit_amount_micros, "
        "gateway_price_id) VALUES (:id, :b, :t, :i, :a, :g) ON CONFLICT (book_id, tier_key, billing_interval) "
        "DO UPDATE SET unit_amount_micros = EXCLUDED.unit_amount_micros, gateway_price_id = EXCLUDED.gateway_price_id"),
        {"id": uuid.uuid4(), "b": book_id, "t": tier_key, "i": interval,
         "a": int(unit_amount_minor) * v.MICROS_PER_MINOR_UNIT, "g": (gateway_price_id or None)})
    db.flush()
    return book_detail(db, book_id)


def delete_entry(db: Session, *, book_id: uuid.UUID, entry_id: uuid.UUID) -> dict[str, Any]:
    book = _book(db, book_id, lock=True)
    if book["status"] != "DRAFT":
        raise RevOpsError(f"price book {book['code']} is {book['status']}", "BOOK_NOT_DRAFT")
    db.execute(text("DELETE FROM plan_price_book_entries WHERE id = :e AND book_id = :b"), {"e": entry_id, "b": book_id})
    db.flush()
    return book_detail(db, book_id)


def _require_tier(db: Session, tier_key: str) -> None:
    if not db.execute(text("SELECT 1 FROM quota_tiers WHERE key = :k AND published_at IS NOT NULL AND is_active"),
                      {"k": tier_key}).first():
        raise RevOpsError(f"no published tier named {tier_key!r}", "BOOK_UNKNOWN_TIER", 422, tier_key=tier_key)


def digest_of(entries: list[dict[str, Any]], currency: str) -> str:
    body = sorted(({"tier_key": e["tier_key"], "interval": e["billing_interval"],
                    "amount_micros": int(e["unit_amount_micros"]), "price_id": e["gateway_price_id"]}
                   for e in entries), key=lambda x: (x["tier_key"], x["interval"]))
    return hashlib.sha256(json.dumps({"currency": currency, "entries": body}, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def publish(db: Session, *, book_id: uuid.UUID, actor_id: Optional[uuid.UUID]) -> dict[str, Any]:
    book = _book(db, book_id, lock=True)
    if book["status"] != "DRAFT":
        raise RevOpsError(f"price book {book['code']} is {book['status']}", "BOOK_NOT_DRAFT")
    detail = book_detail(db, book_id)
    if not detail["entries"]:
        raise RevOpsError("a price book with no entries cannot be published", "BOOK_EMPTY")
    for entry in detail["entries"]:
        _require_tier(db, entry["tier_key"])
    moment = now()
    db.execute(text("UPDATE plan_price_books SET status = 'RETIRED', retired_at = :t WHERE currency = :c "
                    "AND status = 'PUBLISHED'"), {"t": moment, "c": book["currency"]})
    db.execute(text("UPDATE plan_price_books SET status = 'PUBLISHED', published_at = :t, published_by_user_id = :u, "
                    "content_digest = :d WHERE id = :id"),
               {"t": moment, "u": actor_id, "d": digest_of(detail["entries"], book["currency"]), "id": book_id})
    db.flush()
    return book_detail(db, book_id)


def published_prices(db: Session) -> list[PlanPrice]:
    rows = db.execute(text(
        "SELECT e.id, e.tier_key, e.billing_interval, e.unit_amount_micros, e.gateway_price_id, b.currency, b.code "
        "FROM plan_price_book_entries e JOIN plan_price_books b ON b.id = e.book_id WHERE b.status = 'PUBLISHED' "
        "ORDER BY b.currency, e.tier_key, e.billing_interval")).all()
    return [PlanPrice(r[1], r[2], r[5], int(r[3]), r[4], r[0], r[6], "BOOK") for r in rows]


def resolve_price(db: Session, *, tier_key: str, interval: str, currency: str) -> Optional[PlanPrice]:
    for price in published_prices(db):
        if (price.tier_key, price.interval, price.currency) == (tier_key, interval, currency):
            return price
    from app.services import quota_service

    tier = quota_service.published_tier_by_key(db, key=tier_key)
    if (tier is not None and tier.unit_amount_micros is not None and (tier.currency or "USD") == currency
            and (tier.billing_interval or "month") == interval):
        return PlanPrice(tier_key, interval, currency, int(tier.unit_amount_micros), tier.gateway_price_id, None,
                         None, "TIER")
    return None


def tier_for_gateway_price(db: Session, price_id: str) -> Optional[str]:
    """A webhook's product id -> the plan it sells (plan books first, then the tier versions' own prices)."""
    row = db.execute(text("SELECT DISTINCT tier_key FROM plan_price_book_entries WHERE gateway_price_id = :p"),
                     {"p": price_id}).first()
    if row:
        return str(row[0])
    row = db.execute(text("SELECT DISTINCT key FROM quota_tiers WHERE gateway_price_id = :p"), {"p": price_id}).first()
    return str(row[0]) if row else None


__all__ = ["PlanPrice", "book_detail", "create_book", "delete_entry", "digest_of", "list_books", "publish",
           "published_prices", "resolve_price", "set_entry", "tier_for_gateway_price"]
