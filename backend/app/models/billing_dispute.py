"""A chargeback on one of an organization's payments (website and payments session, C5).

Dodo Payments, as merchant of record, answers disputes with the card network; Servabo records what
Dodo reports, tells the operator and the organization, and acts on the outcome. A lost dispute
means the payment was reversed: the organization is read-only ("payment reversed by your bank")
until a later payment succeeds or the operator lifts it (`cleared_at`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

#: Dodo's `dispute_status` values, lowercased.
DISPUTE_STATUSES: tuple[str, ...] = (
    "dispute_opened",
    "dispute_challenged",
    "dispute_accepted",
    "dispute_cancelled",
    "dispute_expired",
    "dispute_won",
    "dispute_lost",
)
#: The outcomes in which the bank kept the money: the payment is reversed.
REVERSED_STATUSES: frozenset[str] = frozenset({"dispute_lost", "dispute_accepted"})


class BillingDispute(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "billing_disputes"
    __table_args__ = (
        UniqueConstraint("gateway", "gateway_dispute_id", name="uq_billing_disputes_gateway_dispute"),
        CheckConstraint("gateway IN ('STRIPE', 'DODO')", name="gateway_known"),
        Index("ix_billing_disputes_organization", "organization_id", "created_at"),
        Index(
            "ix_billing_disputes_reversed_open",
            "organization_id",
            postgresql_where=text("reversed_at IS NOT NULL AND cleared_at IS NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    gateway: Mapped[str] = mapped_column(String(16), nullable=False)
    gateway_dispute_id: Mapped[str] = mapped_column(String(255), nullable=False)
    gateway_payment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: When the payment was reversed (a lost or accepted dispute). Read-only from then...
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    #: ...until a later payment succeeded or the operator lifted it.
    cleared_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cleared_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cleared_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
