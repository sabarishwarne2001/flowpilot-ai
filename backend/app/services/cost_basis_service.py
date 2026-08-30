"""ARCH-18 — what a billable unit costs *us*.

`pricing_service` answers "what do we charge for this". This module answers
"what does it cost us", and the two must resolve against the same price book
version at the same instant, or a margin is computed from two different
moments and means nothing.

That constraint is why the cost basis lives on `price_book_entries` rather
than in a separate rate-card table. A separate table would need its own
versioning, its own effective windows, and its own resolution order — and the
first time those windows disagreed with the price book's, every margin in the
system would be quietly wrong at the seam. Sharing the book's lifecycle means
the two numbers are versioned together by construction: the same
`resolve()` call, the same entry row, the same `effective_from`.

The cost is charged for that. A supplier rate change now requires publishing a
new price book version even when the customer price is unchanged. That is the
right trade — a rate card change IS a change to the margin history's meaning,
and it should leave a versioned artifact.

THE REFUSAL ASYMMETRY, which is the important design decision here:

    pricing_service.resolve() RAISES when no price covers a unit. An unpriced
    call is revenue you cannot invoice and spend you cannot cap, so the whole
    operation is refused.

    cost_basis_service.resolve() RETURNS UNKNOWN. A missing cost basis is a
    reporting gap, not a control failure. Refusing generation because nobody
    filled in a supplier rate would take the platform down to protect a
    dashboard, and the dashboard is honest about the gap anyway.

Getting that backwards in either direction is the failure mode. A cost path
that refuses is an outage; a price path that returns a default is revenue
leakage.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.supplier_cogs import (
    COST_BASIS_SOURCE_VALUES,
    HARD_COST_BASIS_SOURCES,
    SOURCE_ZERO_BYOK,
)
from app.services import pricing_service
from app.services.pricing_service import PriceUnavailableError, ResolvedPrice

logger = logging.getLogger("app.services.cost_basis")


class CostBasisError(Exception):
    """Base class for cost-basis refusals. Deliberately rare."""


class InvalidCostBasisError(CostBasisError):
    """A cost basis was supplied that cannot be stored."""


@dataclass(frozen=True)
class ResolvedCostBasis:
    """The cost side of a resolved price, or an explicit absence of one.

    `known` is the only thing callers should branch on. Reading
    `unit_cost_micros` when `known` is False gives None, and any arithmetic
    that treats that as zero is the bug this class exists to make hard.
    """

    known: bool
    unit_cost_micros: Optional[Decimal]
    source: Optional[str]
    price_book_id: Optional[uuid.UUID]
    price_book_version: Optional[int]
    provider: str
    requested_model: Optional[str]
    entry_model: Optional[str]
    reason: Optional[str] = None

    @property
    def is_hard(self) -> bool:
        """True when the figure is defensible to a finance team.

        ESTIMATED is excluded. It is reported and it is not evidence.
        """
        return self.known and self.source in HARD_COST_BASIS_SOURCES

    def cost_micros(self, quantity: Any) -> Optional[int]:
        """Cost for a quantity, or None. Never 0-as-a-fallback."""
        if not self.known or self.unit_cost_micros is None:
            return None
        product = Decimal(str(quantity)) * self.unit_cost_micros
        return int(product.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def as_details(self) -> dict[str, Any]:
        """The provenance fragment merged into `usage_events.details`.

        The column carries the source too; this carries the *reason* an
        unknown was unknown, which the column has no room for and which is the
        only thing that makes an unknown actionable six months later.
        """
        if self.known:
            payload: dict[str, Any] = {
                "cost_basis_source": self.source,
                "cost_basis_unit_micros": (
                    format(self.unit_cost_micros, "f")
                    if self.unit_cost_micros is not None
                    else None
                ),
            }
            if self.price_book_version is not None:
                payload["cost_basis_book_version"] = self.price_book_version
            return payload
        return {
            "cost_basis_source": None,
            "cost_basis_unknown": True,
            "cost_basis_unknown_reason": self.reason or "unspecified",
        }

    def __repr__(self) -> str:  # pragma: no cover
        if not self.known:
            return f"<ResolvedCostBasis UNKNOWN {self.provider} ({self.reason})>"
        return (
            f"<ResolvedCostBasis {self.provider}/{self.requested_model or '*'} "
            f"{self.unit_cost_micros}µ {self.source}>"
        )


def _unknown(
    *,
    provider: str,
    requested_model: Optional[str],
    reason: str,
    price_book_id: Optional[uuid.UUID] = None,
    price_book_version: Optional[int] = None,
) -> ResolvedCostBasis:
    return ResolvedCostBasis(
        known=False,
        unit_cost_micros=None,
        source=None,
        price_book_id=price_book_id,
        price_book_version=price_book_version,
        provider=provider,
        requested_model=requested_model,
        entry_model=None,
        reason=reason,
    )


def validate_cost_basis(
    cost_basis_micros: Optional[Any],
    cost_basis_source: Optional[str],
) -> tuple[Optional[Decimal], Optional[str]]:
    """Normalise and check a (cost, source) pair before it is written.

    Mirrors the four CHECK constraints so a caller gets a readable Python
    error instead of an IntegrityError from a constraint name. The database
    remains the authority; this is the courtesy layer.
    """
    if cost_basis_micros is None and cost_basis_source is None:
        return None, None

    if (cost_basis_micros is None) != (cost_basis_source is None):
        raise InvalidCostBasisError(
            "cost_basis_micros and cost_basis_source must both be set or both "
            "be None. A cost with no provenance is not reportable, and a "
            "provenance with no cost is not a cost."
        )

    source = str(cost_basis_source)
    if source not in COST_BASIS_SOURCE_VALUES:
        raise InvalidCostBasisError(
            f"'{source}' is not a known cost basis source. Expected one of: "
            f"{', '.join(COST_BASIS_SOURCE_VALUES)}."
        )

    value = Decimal(str(cost_basis_micros))
    if not value.is_finite():
        raise InvalidCostBasisError("cost_basis_micros must be finite.")
    if value < 0:
        raise InvalidCostBasisError(
            f"cost_basis_micros must be >= 0, got {value}. A negative supplier "
            "cost is a credit note, which belongs on a supplier invoice."
        )

    if (value == 0) != (source == SOURCE_ZERO_BYOK):
        raise InvalidCostBasisError(
            "A zero cost basis must be declared as ZERO_BYOK, and ZERO_BYOK "
            "must be zero. An undeclared zero reads downstream as a 100% "
            f"gross margin. Got {value} with source '{source}'. If the "
            "supplier tier is genuinely free, record it as ZERO_BYOK and put "
            "the reason in the entry's notes."
        )

    return value, source


def from_resolved_price(price: ResolvedPrice) -> ResolvedCostBasis:
    """Lift the cost half out of an already-resolved price.

    This is the hot-path entry point. `llm_metering` has already paid for the
    price resolution; taking the cost from the same `ResolvedPrice` guarantees
    both sides came from one book version at one instant, and costs nothing.
    """
    unit_cost = getattr(price, "cost_basis_micros", None)
    source = getattr(price, "cost_basis_source", None)

    if unit_cost is None or source is None:
        return _unknown(
            provider=price.provider,
            requested_model=price.requested_model,
            reason=(
                "price_book_entry_has_no_cost_basis"
                if not price.fallback
                else "price_book_entry_fallback_has_no_cost_basis"
            ),
            price_book_id=price.price_book_id,
            price_book_version=price.price_book_version,
        )

    return ResolvedCostBasis(
        known=True,
        unit_cost_micros=Decimal(str(unit_cost)),
        source=str(source),
        price_book_id=price.price_book_id,
        price_book_version=price.price_book_version,
        provider=price.provider,
        requested_model=price.requested_model,
        entry_model=price.entry_model,
    )


def resolve(
    db: Session,
    *,
    event_type: str,
    provider: str,
    model: Optional[str] = None,
    at: Optional[datetime] = None,
    tier_key: Optional[str] = None,
) -> ResolvedCostBasis:
    """Resolve a cost basis for a unit at an instant.

    Same signature and same lookup discipline as `pricing_service.resolve`,
    including the provider-wide `model IS NULL` fallback — because resolving
    cost against a different entry than the one that produced the price would
    give a margin between two unrelated rows.

    Never raises for an absent cost. See the module docstring.
    """
    moment = at or datetime.now(timezone.utc)
    normalised_provider = (provider or "").strip().lower()

    try:
        price = pricing_service.resolve(
            db,
            event_type=event_type,
            provider=provider,
            model=model,
            at=moment,
            tier_key=tier_key,
        )
    except PriceUnavailableError as exc:
        logger.info(
            "cost_basis.no_price_book",
            extra={
                "event_type": event_type,
                "provider": normalised_provider,
                "model": model,
                "detail": str(exc),
            },
        )
        return _unknown(
            provider=normalised_provider,
            requested_model=model,
            reason="no_price_book_covers_instant",
        )

    basis = from_resolved_price(price)
    if not basis.known:
        logger.info(
            "cost_basis.unknown",
            extra={
                "event_type": event_type,
                "provider": normalised_provider,
                "model": model,
                "price_book_version": price.price_book_version,
                "reason": basis.reason,
            },
        )
    return basis


def coverage(db: Session, *, at: Optional[datetime] = None) -> dict[str, Any]:
    """How much of the price book in force carries a cost basis.

    The number the margin dashboard needs before anyone trusts a margin: if
    the book prices forty units and nineteen have a cost, no gross margin
    computed from it deserves a decimal place.
    """
    moment = at or datetime.now(timezone.utc)
    book = pricing_service.book_in_force(db, at=moment)
    if book is None:
        return {
            "price_book_version": None,
            "entry_count": 0,
            "with_cost_basis": 0,
            "hard_cost_basis": 0,
            "coverage_ratio": None,
        }

    total = 0
    with_basis = 0
    hard = 0
    for snapshot in book.entries.values():
        total += 1
        source = getattr(snapshot, "cost_basis_source", None)
        value = getattr(snapshot, "cost_basis_micros", None)
        if value is not None and source is not None:
            with_basis += 1
            if source in HARD_COST_BASIS_SOURCES:
                hard += 1

    return {
        "price_book_version": book.version,
        "entry_count": total,
        "with_cost_basis": with_basis,
        "hard_cost_basis": hard,
        "coverage_ratio": (with_basis / total) if total else None,
    }


__all__ = [
    "CostBasisError",
    "InvalidCostBasisError",
    "ResolvedCostBasis",
    "coverage",
    "from_resolved_price",
    "resolve",
    "validate_cost_basis",
]