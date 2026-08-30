"""ARCH-14 Step 1 — the platform price book.

This table exists because of finding B1: until now the price applied to every
`usage_events` row came from `ai_settings.input_cost_per_1k_tokens`, a column
a *workspace admin* can write through `PUT /api/v1/ai_settings`. That made
invoices unreproducible and made the `TOTAL_COST_KEY` spend ceiling bypassable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

DEFAULT_CURRENCY: str = "USD"

# ARCH-18. Imported rather than restated so the CHECK below and the service
# layer cannot disagree about what a legal source is. supplier_cogs imports
# nothing from this module, so there is no cycle.
from app.models.supplier_cogs import COST_BASIS_SOURCE_VALUES  # noqa: E402

_COST_BASIS_SOURCE_IN = ", ".join(f"'{v}'" for v in COST_BASIS_SOURCE_VALUES)


class PriceBook(Base, UUIDMixin, TimestampMixin):
    """One published, immutable version of the platform price list."""

    __tablename__ = "price_books"

    __table_args__ = (
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="effective_window_ordered",
        ),
        CheckConstraint("length(currency) = 3", name="currency_iso4217"),
        CheckConstraint(
            "NOT is_active OR published_at IS NOT NULL",
            name="active_implies_published",
        ),
        CheckConstraint(
            "(published_at IS NULL) = (content_digest IS NULL)",
            name="digest_iff_published",
        ),
        Index("uq_price_books_version", "version", unique=True),
        Index(
            "ix_price_books_effective",
            "effective_from",
            "effective_to",
            postgresql_where=text("is_active AND published_at IS NOT NULL"),
        ),
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False)

    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_to: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text(f"'{DEFAULT_CURRENCY}'")
    )

    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    content_digest: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    entries: Mapped[list["PriceBookEntry"]] = relationship(
        "PriceBookEntry",
        back_populates="price_book",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def is_published(self) -> bool:
        return self.published_at is not None

    def __repr__(self) -> str:  # pragma: no cover
        state = "published" if self.is_published else "draft"
        return f"<PriceBook v{self.version} {state} from={self.effective_from}>"


class PriceBookEntry(Base, UUIDMixin, TimestampMixin):
    """One priced line: (event_type, provider, model, tier_key) -> price."""

    __tablename__ = "price_book_entries"

    __table_args__ = (
        CheckConstraint("unit_price_micros >= 0", name="price_non_negative"),
        CheckConstraint("length(event_type) > 0", name="event_type_not_blank"),
        CheckConstraint("length(provider) > 0", name="provider_not_blank"),
        CheckConstraint("length(unit) > 0", name="unit_not_blank"),
        # ---- ARCH-18: the supplier rate card ------------------------------
        CheckConstraint(
            "(cost_basis_micros IS NULL) = (cost_basis_source IS NULL)",
            name="cost_basis_pair_complete",
        ),
        CheckConstraint(
            "cost_basis_micros IS NULL OR cost_basis_micros >= 0",
            name="cost_basis_non_negative",
        ),
        CheckConstraint(
            "cost_basis_source IS NULL OR cost_basis_source IN "
            f"({_COST_BASIS_SOURCE_IN})",
            name="cost_basis_source_known",
        ),
        CheckConstraint(
            "cost_basis_micros IS NULL OR "
            "(cost_basis_micros = 0) = (cost_basis_source = 'ZERO_BYOK')",
            name="zero_cost_is_declared",
        ),
        Index(
            "ix_price_book_entries_lookup",
            "price_book_id",
            "event_type",
            "provider",
        ),
    )

    price_book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("price_books.id", ondelete="CASCADE"),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tier_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)

    unit_price_micros: Mapped[Decimal] = mapped_column(
        Numeric(20, 9), nullable=False
    )

    # ---- ARCH-18 ----------------------------------------------------------
    # What the supplier charges us for this unit, against what we charge for
    # it above. Same precision so the two are directly comparable.
    #
    # NULL is the honest answer for every entry published before ARCH-18, and
    # it stays NULL forever: price_book_entries_publish_immutable() refuses
    # every UPDATE once the parent book is published. Cost basis arrives by
    # publishing version N+1, not by backfill.
    cost_basis_micros: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 9), nullable=True
    )
    cost_basis_source: Mapped[Optional[str]] = mapped_column(
        String(24), nullable=True
    )

    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    price_book: Mapped[PriceBook] = relationship(
        "PriceBook", back_populates="entries"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PriceBookEntry {self.event_type} {self.provider}/"
            f"{self.model or '*'} @{self.unit_price_micros}µ/{self.unit}>"
        )


__all__ = ["DEFAULT_CURRENCY", "PriceBook", "PriceBookEntry"]