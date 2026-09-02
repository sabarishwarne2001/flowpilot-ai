"""ARCH-14 Step 1 — price resolution. The only module that decides what a billable unit costs."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.usage_events import USAGE_EVENT_TYPES
from app.models.price_book import DEFAULT_CURRENCY, PriceBook, PriceBookEntry

logger = logging.getLogger("app.services.pricing")

_ANY = ""
_MICROS_QUANTUM = Decimal("0.000000001")


class PricingError(Exception):
    """Base class for price-path refusals."""


class PriceUnavailableError(PricingError):
    """No published book, or no entry, prices this unit at this instant."""


class PriceBookValidationError(PricingError):
    """A book was submitted for publication that cannot be published."""


@dataclass(frozen=True)
class ResolvedPrice:
    price_book_id: uuid.UUID
    price_book_version: int
    event_type: str
    provider: str
    entry_model: Optional[str]
    requested_model: Optional[str]
    unit: str
    unit_price_micros: Decimal
    currency: str
    fallback: bool

    # ---- ARCH-18 -------------------------------------------------------
    # The cost side of the same entry, carried on the same object so a
    # caller cannot resolve price at one instant and cost at another.
    # Defaulted to None so every existing construction site keeps working;
    # None means unknown and must never be read as zero.
    cost_basis_micros: Optional[Decimal] = None
    cost_basis_source: Optional[str] = None

    def cost_micros(self, quantity: Any) -> int:
        return cost_micros(quantity, self.unit_price_micros)

    def cost_basis_for(self, quantity: Any) -> Optional[int]:
        """Supplier cost for a quantity, or None when unknown.

        Deliberately not named cost_micros_*: `cost_micros` on this class is
        already the revenue figure, and two methods a character apart that
        mean revenue and COGS is a bug waiting for a tired afternoon.
        """
        if self.cost_basis_micros is None:
            return None
        product = Decimal(str(quantity)) * self.cost_basis_micros
        return int(product.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def as_details(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "price_source": "price_book",
            "price_book_version": self.price_book_version,
            "unit_price_micros": format(self.unit_price_micros, "f"),
            "currency": self.currency,
        }
        if self.fallback:
            payload["price_fallback"] = True
            payload["price_fallback_from"] = self.requested_model or "unknown"
        if self.cost_basis_source is not None:
            payload["cost_basis_source"] = self.cost_basis_source
            payload["cost_basis_unit_micros"] = format(
                self.cost_basis_micros or Decimal(0), "f"
            )
        else:
            payload["cost_basis_unknown"] = True
        return payload

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ResolvedPrice v{self.price_book_version} {self.event_type} "
            f"{self.provider}/{self.requested_model or '*'} "
            f"{self.unit_price_micros}µ/{self.unit}"
            f"{' FALLBACK' if self.fallback else ''}>"
        )


@dataclass(frozen=True)
class PriceSpec:
    event_type: str
    provider: str
    unit_price_micros: Decimal
    unit: Optional[str] = None
    model: Optional[str] = None
    tier_key: Optional[str] = None
    notes: Optional[str] = None

    # ---- ARCH-18 -------------------------------------------------------
    # Optional, and it must stay optional: making a cost basis mandatory
    # would mean no price book can be published until every supplier rate is
    # known, which turns a reporting gap into an inability to change prices.
    # An entry published without one honestly reports "unknown" forever.
    cost_basis_micros: Optional[Decimal] = None
    cost_basis_source: Optional[str] = None


def cost_micros(quantity: Any, unit_price_micros: Any) -> int:
    product = Decimal(str(quantity)) * Decimal(str(unit_price_micros))
    return int(product.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def per_1k_from_unit_micros(unit_price_micros: Any) -> float:
    return float(Decimal(str(unit_price_micros)) * 1000 / Decimal(1_000_000))


@dataclass(frozen=True)
class _EntrySnapshot:
    unit: str
    unit_price_micros: Decimal
    model: Optional[str]
    cost_basis_micros: Optional[Decimal] = None
    cost_basis_source: Optional[str] = None


@dataclass(frozen=True)
class _BookSnapshot:
    id: uuid.UUID
    version: int
    currency: str
    effective_from: datetime
    effective_to: Optional[datetime]
    entries: Mapping[tuple[str, str, str, str], _EntrySnapshot]

    def covers(self, at: datetime) -> bool:
        if at < self.effective_from:
            return False
        return self.effective_to is None or at < self.effective_to


_cache_lock = threading.Lock()
_cache: Optional[tuple[float, tuple[_BookSnapshot, ...]]] = None


def clear_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def _snapshot(book: PriceBook) -> _BookSnapshot:
    entries: dict[tuple[str, str, str, str], _EntrySnapshot] = {}
    for entry in book.entries:
        key = (
            entry.event_type,
            entry.provider,
            entry.model or _ANY,
            entry.tier_key or _ANY,
        )
        raw_cost = getattr(entry, "cost_basis_micros", None)
        entries[key] = _EntrySnapshot(
            unit=entry.unit,
            unit_price_micros=Decimal(str(entry.unit_price_micros)),
            model=entry.model,
            cost_basis_micros=(
                Decimal(str(raw_cost)) if raw_cost is not None else None
            ),
            cost_basis_source=getattr(entry, "cost_basis_source", None),
        )
    return _BookSnapshot(
        id=book.id,
        version=book.version,
        currency=book.currency,
        effective_from=_as_utc(book.effective_from),
        effective_to=_as_utc(book.effective_to) if book.effective_to else None,
        entries=entries,
    )


def _load(db: Session) -> tuple[_BookSnapshot, ...]:
    books = (
        db.execute(
            select(PriceBook)
            .options(selectinload(PriceBook.entries))
            .where(
                PriceBook.is_active.is_(True),
                PriceBook.published_at.is_not(None),
            )
            .order_by(PriceBook.version.desc())
        )
        .scalars()
        .all()
    )
    return tuple(_snapshot(b) for b in books)


def _books(db: Session) -> tuple[_BookSnapshot, ...]:
    global _cache
    ttl = float(getattr(settings, "PRICE_BOOK_CACHE_TTL_SECONDS", 300.0) or 0.0)
    if ttl <= 0:
        return _load(db)

    now = time.monotonic()
    with _cache_lock:
        if _cache is not None and (now - _cache[0]) < ttl:
            return _cache[1]

    loaded = _load(db)
    with _cache_lock:
        _cache = (time.monotonic(), loaded)
    return loaded


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        logger.warning("pricing.naive_datetime_coerced", extra={"value": str(value)})
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def book_in_force(db: Session, *, at: datetime) -> Optional[_BookSnapshot]:
    moment = _as_utc(at)
    covering = [b for b in _books(db) if b.covers(moment)]
    if not covering:
        return None
    if len(covering) > 1:
        covering.sort(key=lambda b: b.version, reverse=True)
        logger.error(
            "pricing.overlapping_books",
            extra={
                "at": moment.isoformat(),
                "versions": [b.version for b in covering],
                "chosen": covering[0].version,
            },
        )
    return covering[0]


def resolve(
    db: Session,
    *,
    event_type: str,
    provider: str,
    model: Optional[str] = None,
    at: Optional[datetime] = None,
    tier_key: Optional[str] = None,
) -> ResolvedPrice:
    moment = _as_utc(at or datetime.now(timezone.utc))
    normalised_provider = (provider or "").strip().lower()

    book = book_in_force(db, at=moment)
    if book is None:
        raise PriceUnavailableError(
            f"No active published price book covers {moment.isoformat()}. "
            "Publish one with scripts/seed_price_book.py before serving "
            "billable traffic; refusing rather than pricing at zero."
        )

    exact = book.entries.get(
        (event_type, normalised_provider, model or _ANY, tier_key or _ANY)
    )
    fallback = False
    entry = exact
    if entry is None and model:
        entry = book.entries.get(
            (event_type, normalised_provider, _ANY, tier_key or _ANY)
        )
        fallback = entry is not None
    if entry is None and tier_key:
        entry = book.entries.get((event_type, normalised_provider, model or _ANY, _ANY))
        if entry is None:
            entry = book.entries.get((event_type, normalised_provider, _ANY, _ANY))
            fallback = entry is not None

    if entry is None:
        raise PriceUnavailableError(
            f"Price book v{book.version} has no entry for "
            f"{event_type}/{normalised_provider}/{model or '*'} "
            f"(tier={tier_key or '*'}) and no provider-wide default. "
            "This is a refusal, not a zero price."
        )

    if fallback:
        logger.warning(
            "pricing.model_fallback",
            extra={
                "event_type": event_type,
                "provider": normalised_provider,
                "requested_model": model,
                "price_book_version": book.version,
            },
        )

    return ResolvedPrice(
        price_book_id=book.id,
        price_book_version=book.version,
        event_type=event_type,
        provider=normalised_provider,
        entry_model=entry.model,
        requested_model=model,
        unit=entry.unit,
        unit_price_micros=entry.unit_price_micros,
        currency=book.currency,
        fallback=fallback,
        cost_basis_micros=entry.cost_basis_micros,
        cost_basis_source=entry.cost_basis_source,
    )


def display_prices_per_1k(
    db: Session,
    *,
    provider: str,
    model: Optional[str],
    at: Optional[datetime] = None,
) -> tuple[float, float]:
    out: list[float] = []
    for event_type in ("llm.input_token", "llm.output_token"):
        try:
            price = resolve(
                db, event_type=event_type, provider=provider, model=model, at=at
            )
            out.append(per_1k_from_unit_micros(price.unit_price_micros))
        except PriceUnavailableError:
            logger.warning(
                "pricing.display_unavailable",
                extra={"event_type": event_type, "provider": provider, "model": model},
            )
            out.append(0.0)
    return out[0], out[1]


def content_digest(
    *,
    version: int,
    currency: str,
    effective_from: datetime,
    entries: Sequence[PriceSpec],
) -> str:
    """SHA-256 over the canonical price content.

    ARCH-18 deliberately does NOT fold `cost_basis_micros` into this digest,
    and the reason is not that cost is unimportant.

    The digest is what `verify_digest` checks and what ARCH-14's verification
    gate compares against a recorded value to prove a published book has not
    been mutated. Adding a field to the canonical form changes the hash of
    every book ever published, so every existing book would fail verification
    on the first run after this deploy — an alert storm that says "your price
    books were tampered with" when nothing was touched. There is no migration
    that fixes it, because the whole point of the digest is that it cannot be
    recomputed and re-stored without destroying its own guarantee.

    What the digest attests to is what the customer was charged. That is the
    thing a customer disputes and the thing a court would ask about. Cost
    basis is internal, is protected by the same publish-immutability trigger
    as every other column on the entry, and is reconciled monthly against
    supplier invoices — which is a stronger check than a hash anyway.
    """
    canonical = {
        "version": version,
        "currency": currency,
        "effective_from": _as_utc(effective_from).isoformat(),
        "entries": sorted(
            [
                [
                    e.event_type,
                    e.provider,
                    e.model or "",
                    e.tier_key or "",
                    e.unit or "",
                    format(
                        Decimal(str(e.unit_price_micros)).quantize(_MICROS_QUANTUM),
                        "f",
                    ),
                ]
                for e in entries
            ]
        ),
    }
    blob = json.dumps(canonical, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _validate(entries: Sequence[PriceSpec]) -> list[PriceSpec]:
    if not entries:
        raise PriceBookValidationError("A price book with no entries prices nothing.")

    seen: set[tuple[str, str, str, str]] = set()
    resolved: list[PriceSpec] = []

    for spec in entries:
        descriptor = USAGE_EVENT_TYPES.get(spec.event_type)
        if descriptor is None:
            raise PriceBookValidationError(
                f"'{spec.event_type}' is not in the ARCH-10 usage vocabulary. "
                "Add it to app/core/usage_events.py first."
            )

        unit = spec.unit or descriptor.unit.value
        if unit != descriptor.unit.value:
            raise PriceBookValidationError(
                f"'{spec.event_type}' is metered in {descriptor.unit.value!r}, "
                f"not {unit!r}."
            )

        provider = (spec.provider or "").strip().lower()
        if not provider:
            raise PriceBookValidationError(
                f"Entry for '{spec.event_type}' has no provider."
            )

        price = Decimal(str(spec.unit_price_micros))
        if price < 0:
            raise PriceBookValidationError(
                f"Negative price for '{spec.event_type}'/{provider}."
            )

        key = (spec.event_type, provider, spec.model or _ANY, spec.tier_key or _ANY)
        if key in seen:
            raise PriceBookValidationError(
                f"Duplicate entry {key!r}."
            )
        seen.add(key)

        # ARCH-18. Imported here rather than at module scope: cost_basis_service
        # imports pricing_service, and a top-level import either way round is a
        # cycle. The validator is pure, so the deferred import costs nothing
        # after the first call.
        from app.services.cost_basis_service import (
            InvalidCostBasisError,
            validate_cost_basis,
        )

        try:
            cost_basis, cost_source = validate_cost_basis(
                spec.cost_basis_micros, spec.cost_basis_source
            )
        except InvalidCostBasisError as exc:
            raise PriceBookValidationError(
                f"Entry {key!r}: {exc}"
            ) from exc

        resolved.append(
            PriceSpec(
                event_type=spec.event_type,
                provider=provider,
                unit_price_micros=price.quantize(_MICROS_QUANTUM),
                unit=unit,
                model=spec.model,
                tier_key=spec.tier_key,
                notes=spec.notes,
                cost_basis_micros=(
                    cost_basis.quantize(_MICROS_QUANTUM)
                    if cost_basis is not None
                    else None
                ),
                cost_basis_source=cost_source,
            )
        )

    return resolved


def publish(
    db: Session,
    *,
    version: int,
    effective_from: datetime,
    entries: Sequence[PriceSpec],
    published_by_user_id: Optional[uuid.UUID] = None,
    currency: str = DEFAULT_CURRENCY,
    notes: Optional[str] = None,
    close_predecessor: bool = True,
) -> PriceBook:
    effective_from = _as_utc(effective_from)
    validated = _validate(entries)

    clash = db.execute(
        select(PriceBook.id).where(PriceBook.version == version)
    ).first()
    if clash is not None:
        raise PriceBookValidationError(
            f"Price book version {version} already exists."
        )

    book = PriceBook(
        version=version,
        effective_from=effective_from,
        effective_to=None,
        currency=currency,
        is_active=False,
        notes=notes,
    )
    db.add(book)
    db.flush([book])

    for spec in validated:
        db.add(
            PriceBookEntry(
                price_book_id=book.id,
                event_type=spec.event_type,
                provider=spec.provider,
                model=spec.model,
                tier_key=spec.tier_key,
                unit=spec.unit or "",
                unit_price_micros=spec.unit_price_micros,
                cost_basis_micros=spec.cost_basis_micros,
                cost_basis_source=spec.cost_basis_source,
                notes=spec.notes,
            )
        )
    db.flush()

    if close_predecessor:
        predecessor = (
            db.execute(
                select(PriceBook)
                .where(
                    PriceBook.is_active.is_(True),
                    PriceBook.published_at.is_not(None),
                    PriceBook.effective_to.is_(None),
                    PriceBook.effective_from < effective_from,
                )
                .order_by(PriceBook.version.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if predecessor is not None:
            predecessor.effective_to = effective_from
            db.flush([predecessor])
            logger.info(
                "pricing.book_closed",
                extra={
                    "version": predecessor.version,
                    "effective_to": effective_from.isoformat(),
                },
            )

    book.published_at = datetime.now(timezone.utc)
    book.published_by_user_id = published_by_user_id
    book.content_digest = content_digest(
        version=version,
        currency=currency,
        effective_from=effective_from,
        entries=validated,
    )
    book.is_active = True
    db.flush([book])

    clear_cache()
    logger.info(
        "pricing.book_published",
        extra={
            "price_book_id": str(book.id),
            "version": version,
            "entries": len(validated),
            "entries_with_cost_basis": sum(
                1 for s in validated if s.cost_basis_micros is not None
            ),
            "effective_from": effective_from.isoformat(),
            "content_digest": book.content_digest,
        },
    )
    return book


def specs_from_book(book: PriceBook) -> list[PriceSpec]:
    return [
        PriceSpec(
            event_type=e.event_type,
            provider=e.provider,
            unit_price_micros=Decimal(str(e.unit_price_micros)),
            unit=e.unit,
            model=e.model,
            tier_key=e.tier_key,
            notes=e.notes,
            cost_basis_micros=(
                Decimal(str(e.cost_basis_micros))
                if getattr(e, "cost_basis_micros", None) is not None
                else None
            ),
            cost_basis_source=getattr(e, "cost_basis_source", None),
        )
        for e in book.entries
    ]


def verify_digest(book: PriceBook) -> bool:
    if book.published_at is None or not book.content_digest:
        return False
    return book.content_digest == content_digest(
        version=book.version,
        currency=book.currency,
        effective_from=book.effective_from,
        entries=specs_from_book(book),
    )


__all__ = [
    "PriceBookValidationError",
    "PriceSpec",
    "PriceUnavailableError",
    "PricingError",
    "ResolvedPrice",
    "book_in_force",
    "clear_cache",
    "content_digest",
    "cost_micros",
    "display_prices_per_1k",
    "per_1k_from_unit_micros",
    "publish",
    "resolve",
    "specs_from_book",
    "verify_digest",
]
