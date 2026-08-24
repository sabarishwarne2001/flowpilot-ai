"""ARCH-15 Step 15.5 — the frozen invoice (A9).

WHAT MAKES THIS ROW DIFFERENT FROM EVERY OTHER CACHE IN THE PHASE
================================================================

`subscriptions` is a cache of Stripe, reconciled by re-fetch. `invoices` is the
opposite: it is the **authority**, and Stripe's copy is the cache. Once
`finalized_at` is set, the numbers here are what we sent, and the only
sanctioned way to change them is to VOID and reassemble.

The scenario the whole table is shaped around:

> A customer disputes a charge eleven months later. Spend limits, price books
> and tier definitions have all changed since. Reproduce the invoice.

Three properties make that a lookup rather than an argument:

1. **Line items are frozen, not joined.** `unit_price_micros` is copied onto
   the line. `price_book_entry_id` is provenance — "this came from that entry"
   — and the number on the line is the truth. Reading the price through the FK
   would mean a price book publication silently changed an issued invoice.
2. **The provenance triple is frozen.** `price_book_id`, `quota_tier_id`,
   `seats_billed`, all with `ON DELETE RESTRICT` on the first two, so the
   versions that produced these numbers cannot be deleted while the invoice
   exists.
3. **`content_digest` covers the lines.** Recompute on read and compare; a
   mismatch is an integrity incident of the same shape as ARCH-07's audit
   chain, not a rendering difference.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.billing_account import BillingAccount
    from app.models.price_book import PriceBook, PriceBookEntry
    from app.models.quota_tier import QuotaTier
    from app.models.subscription import Subscription

INVOICE_STATUS_ENUM_NAME: str = "invoice_status"
INVOICE_LINE_KIND_ENUM_NAME: str = "invoice_line_kind"

#: The prefix on `content_digest`. Explicit so that the day a second algorithm
#: is needed, existing rows say which one produced them instead of being
#: 64 anonymous hex characters.
DIGEST_PREFIX: str = "sha256:"


class InvoiceStatus(str, PyEnum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    PAID = "PAID"
    VOID = "VOID"
    UNCOLLECTIBLE = "UNCOLLECTIBLE"


class InvoiceLineKind(str, PyEnum):
    SEAT = "SEAT"
    INCLUDED = "INCLUDED"
    OVERAGE = "OVERAGE"
    CREDIT = "CREDIT"
    TAX = "TAX"


INVOICE_STATUS_VALUES: tuple[str, ...] = tuple(m.value for m in InvoiceStatus)
INVOICE_LINE_KIND_VALUES: tuple[str, ...] = tuple(m.value for m in InvoiceLineKind)

#: Statuses that mean money is owed. The dunning engine's input set.
COLLECTIBLE_INVOICE_STATUSES: tuple[InvoiceStatus, ...] = (InvoiceStatus.OPEN,)

#: Statuses past which the row is frozen.
FINALIZED_INVOICE_STATUSES: tuple[InvoiceStatus, ...] = (
    InvoiceStatus.OPEN,
    InvoiceStatus.PAID,
    InvoiceStatus.VOID,
    InvoiceStatus.UNCOLLECTIBLE,
)

#: Columns the immutability trigger permits changing after finalization.
#: Kept here as well as in the trigger because Gate 15.5 asserts the two
#: agree — a Python-side write to a frozen column should fail loudly in a test
#: rather than at 2am against production.
MUTABLE_AFTER_FINALIZE: frozenset[str] = frozenset(
    {
        "status",
        "amount_paid_micros",
        "stripe_invoice_id",
        "paid_at",
        "issued_at",
        "assembly_notes",
        "updated_at",
    }
)


class Invoice(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "invoices"

    __table_args__ = (
        UniqueConstraint("number", name="uq_invoices_number"),
        UniqueConstraint("stripe_invoice_id", name="uq_invoices_stripe_invoice_id"),
        CheckConstraint(
            "total_micros = subtotal_micros + tax_micros",
            name="ck_invoices_total_is_subtotal_plus_tax",
        ),
        CheckConstraint("period_end > period_start", name="ck_invoices_period_ordered"),
        CheckConstraint(
            "finalized_at IS NULL OR content_digest <> ''",
            name="ck_invoices_finalized_has_digest",
        ),
        CheckConstraint(
            "amount_paid_micros >= 0 AND amount_paid_micros <= total_micros",
            name="ck_invoices_paid_within_total",
        ),
        CheckConstraint("seats_billed >= 0", name="ck_invoices_seats_non_negative"),
        CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_invoices_currency_iso4217",
        ),
        CheckConstraint(
            "content_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_invoices_digest_shape",
        ),
        CheckConstraint(
            f"status <> 'PAID'::{INVOICE_STATUS_ENUM_NAME} OR paid_at IS NOT NULL",
            name="ck_invoices_paid_has_paid_at",
        ),
        CheckConstraint(
            f"status = 'DRAFT'::{INVOICE_STATUS_ENUM_NAME} "
            "OR finalized_at IS NOT NULL",
            name="ck_invoices_non_draft_is_finalized",
        ),
        Index("ix_invoices_account_period", "billing_account_id", "period_start"),
        Index(
            "ix_invoices_account_created",
            "billing_account_id",
            text("created_at DESC"),
        ),
        Index("ix_invoices_subscription_id", "subscription_id"),
        Index("ix_invoices_price_book_id", "price_book_id"),
        Index("ix_invoices_quota_tier_id", "quota_tier_id"),
        Index(
            "ix_invoices_open",
            text("period_end DESC"),
            postgresql_where=text(f"status = 'OPEN'::{INVOICE_STATUS_ENUM_NAME}"),
        ),
        Index(
            "uq_invoices_subscription_period",
            "subscription_id",
            "period_start",
            unique=True,
            postgresql_where=text(
                "subscription_id IS NOT NULL AND status <> "
                f"'VOID'::{INVOICE_STATUS_ENUM_NAME}"
            ),
        ),
    )

    billing_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subscription_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="RESTRICT"),
        nullable=True,
        doc=(
            "Nullable: a usage-only period with no live subscription still "
            "deserves a reproducible document."
        ),
    )
    stripe_invoice_id: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc=(
            "Nullable because an invoice is assembled before Stripe finalises "
            "one, and because a zero-total period never produces a Stripe "
            "invoice at all."
        ),
    )

    number: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Ours, not Stripe's. `FP-YYYYMM-NNNNNN` from `invoice_number_seq`.",
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        PGEnum(
            InvoiceStatus,
            name=INVOICE_STATUS_ENUM_NAME,
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text(f"'DRAFT'::{INVOICE_STATUS_ENUM_NAME}"),
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    subtotal_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    tax_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    total_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    amount_paid_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    # ---- A9: the provenance triple, frozen ------------------------------
    price_book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("price_books.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quota_tier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quota_tiers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    seats_billed: Mapped[int] = mapped_column(Integer, nullable=False)

    content_digest: Mapped[str] = mapped_column(String(71), nullable=False)

    issued_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finalized_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    assembly_notes: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        doc=(
            "What the assembler could not do cleanly — an unpriceable seat "
            "line, a period containing unreproducible legacy usage. Recorded "
            "rather than swallowed, because the honest answer to a dispute is "
            "sometimes 'this part we cannot defend'."
        ),
    )

    billing_account: Mapped["BillingAccount"] = relationship(
        "BillingAccount", lazy="joined"
    )
    subscription: Mapped[Optional["Subscription"]] = relationship(
        "Subscription", lazy="select"
    )
    price_book: Mapped["PriceBook"] = relationship("PriceBook", lazy="select")
    quota_tier: Mapped["QuotaTier"] = relationship("QuotaTier", lazy="select")
    line_items: Mapped[list["InvoiceLineItem"]] = relationship(
        "InvoiceLineItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLineItem.line_number",
    )

    @property
    def is_finalized(self) -> bool:
        return self.finalized_at is not None

    @property
    def amount_due_micros(self) -> int:
        return max(0, int(self.total_micros) - int(self.amount_paid_micros))

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Invoice {self.number} {self.status.value if self.status else None} "
            f"{self.total_micros}µ {self.currency}>"
        )


class InvoiceLineItem(Base, UUIDMixin):
    __tablename__ = "invoice_line_items"

    __table_args__ = (
        UniqueConstraint(
            "invoice_id", "line_number", name="uq_invoice_line_items_number"
        ),
        CheckConstraint(
            "amount_micros = round(quantity * unit_price_micros)",
            name="ck_invoice_line_amount_matches",
        ),
        CheckConstraint("quantity >= 0", name="ck_invoice_line_quantity_non_negative"),
        CheckConstraint("line_number >= 1", name="ck_invoice_line_number_positive"),
        CheckConstraint(
            f"kind <> 'INCLUDED'::{INVOICE_LINE_KIND_ENUM_NAME} "
            "OR (unit_price_micros = 0 AND amount_micros = 0)",
            name="ck_invoice_line_included_is_free",
        ),
        CheckConstraint(
            f"kind <> 'OVERAGE'::{INVOICE_LINE_KIND_ENUM_NAME} "
            "OR limit_key IS NOT NULL",
            name="ck_invoice_line_overage_names_a_limit",
        ),
        Index("ix_invoice_line_items_invoice_id", "invoice_id"),
        Index("ix_invoice_line_items_price_book_entry_id", "price_book_entry_id"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[InvoiceLineKind] = mapped_column(
        PGEnum(
            InvoiceLineKind,
            name=INVOICE_LINE_KIND_ENUM_NAME,
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)

    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    unit_price_micros: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        doc="Frozen. Copied from the price book entry, never read through it.",
    )
    amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)

    price_book_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("price_book_entries.id", ondelete="RESTRICT"),
        nullable=True,
        doc="Provenance only. The number above is the truth.",
    )
    usage_event_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    limit_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    included_quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
        doc="The allowance this overage was measured against, for the dispute.",
    )
    estimated_quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
        doc=(
            "ARCH-14 §14.7's disclosure, carried onto the bill. A customer is "
            "entitled to know which part of a charge is an estimate before "
            "they ask, not during a dispute."
        ),
    )

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="line_items")
    price_book_entry: Mapped[Optional["PriceBookEntry"]] = relationship(
        "PriceBookEntry", lazy="select"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<InvoiceLineItem #{self.line_number} "
            f"{self.kind.value if self.kind else None} "
            f"{self.quantity}×{self.unit_price_micros}={self.amount_micros}µ>"
        )


__all__ = [
    "COLLECTIBLE_INVOICE_STATUSES",
    "DIGEST_PREFIX",
    "FINALIZED_INVOICE_STATUSES",
    "INVOICE_LINE_KIND_ENUM_NAME",
    "INVOICE_LINE_KIND_VALUES",
    "INVOICE_STATUS_ENUM_NAME",
    "INVOICE_STATUS_VALUES",
    "MUTABLE_AFTER_FINALIZE",
    "Invoice",
    "InvoiceLineKind",
    "InvoiceLineItem",
    "InvoiceStatus",
]