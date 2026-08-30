"""ARCH-18 — supplier invoices and the monthly reconciliation loop."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

DEFAULT_CURRENCY: str = "USD"

COST_BASIS_SOURCE_VALUES: tuple[str, ...] = (
    "SUPPLIER_RATE_CARD",
    "MEASURED",
    "ESTIMATED",
    "ZERO_BYOK",
)

SOURCE_SUPPLIER_RATE_CARD: str = "SUPPLIER_RATE_CARD"
SOURCE_MEASURED: str = "MEASURED"
SOURCE_ESTIMATED: str = "ESTIMATED"
SOURCE_ZERO_BYOK: str = "ZERO_BYOK"

HARD_COST_BASIS_SOURCES: frozenset[str] = frozenset(
    {SOURCE_SUPPLIER_RATE_CARD, SOURCE_MEASURED, SOURCE_ZERO_BYOK}
)

RECONCILIATION_STATUS_VALUES: tuple[str, ...] = (
    "MATCHED",
    "INVESTIGATE",
    "ACCEPTED",
)

STATUS_MATCHED: str = "MATCHED"
STATUS_INVESTIGATE: str = "INVESTIGATE"
STATUS_ACCEPTED: str = "ACCEPTED"

_STATUS_IN = ", ".join(f"'{v}'" for v in RECONCILIATION_STATUS_VALUES)


class SupplierInvoice(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "supplier_invoices"

    __table_args__ = (
        CheckConstraint("period_end >= period_start", name="period_ordered"),
        CheckConstraint("invoiced_total_micros >= 0", name="total_non_negative"),
        CheckConstraint("length(currency) = 3", name="currency_iso4217"),
        CheckConstraint("length(provider) > 0", name="provider_not_blank"),
        Index(
            "uq_supplier_invoices_provider_period",
            "provider",
            "period_start",
            "period_end",
            unique=True,
        ),
        Index("ix_supplier_invoices_period_start", "period_start"),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    invoice_reference: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        doc="The supplier's own invoice number.",
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(
        Date, nullable=False, doc="Last day covered, INCLUSIVE."
    )
    invoiced_total_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text(f"'{DEFAULT_CURRENCY}'")
    )
    raw_document_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="RESTRICT"),
        nullable=True,
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    ingested_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    reconciliations: Mapped[list["SupplierReconciliation"]] = relationship(
        "SupplierReconciliation",
        back_populates="supplier_invoice",
        order_by="SupplierReconciliation.reconciled_at.desc()",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SupplierInvoice {self.provider} {self.period_start}.."
            f"{self.period_end} {self.invoiced_total_micros}µ>"
        )


class SupplierReconciliation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "supplier_reconciliations"

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_IN})", name="status_known"),
        CheckConstraint("modelled_total_micros >= 0", name="modelled_non_negative"),
        CheckConstraint(
            "(modelled_total_micros = 0) = (variance_ratio IS NULL)",
            name="ratio_iff_modelled",
        ),
        Index(
            "ix_supplier_reconciliations_invoice",
            "supplier_invoice_id",
            "reconciled_at",
        ),
    )

    supplier_invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "supplier_invoices.id",
            ondelete="RESTRICT",
            name="fk_supplier_reconciliations_invoice",
        ),
        nullable=False,
    )

    modelled_total_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    variance_micros: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        doc="invoiced - modelled. Positive means we under-modelled our cost.",
    )
    variance_ratio: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 6),
        nullable=True,
        doc="variance / modelled. NULL when modelled is zero.",
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    modelled_event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    unknown_cost_event_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reconciled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    reconciled_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    supplier_invoice: Mapped[SupplierInvoice] = relationship(
        "SupplierInvoice", back_populates="reconciliations"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SupplierReconciliation {self.status} "
            f"variance={self.variance_micros}µ ratio={self.variance_ratio}>"
        )


__all__ = [
    "COST_BASIS_SOURCE_VALUES",
    "DEFAULT_CURRENCY",
    "HARD_COST_BASIS_SOURCES",
    "RECONCILIATION_STATUS_VALUES",
    "SOURCE_ESTIMATED",
    "SOURCE_MEASURED",
    "SOURCE_SUPPLIER_RATE_CARD",
    "SOURCE_ZERO_BYOK",
    "STATUS_ACCEPTED",
    "STATUS_INVESTIGATE",
    "STATUS_MATCHED",
    "SupplierInvoice",
    "SupplierReconciliation",
]