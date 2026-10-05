"""PHASE 4 — payment-risk flags: a vendor's bank account changed; a suspiciously round total.

Kept apart from `anomaly_findings` on purpose: ARCH-34's finding vocabulary
(kinds, layers) is pinned by its migration and its verification gate, and
these are not cross-document duplicates or price surges. Same plan
capability (the radar), same review verbs (confirm / dismiss with a reason).

`details` never holds a full account number: the old and new accounts are
stored masked (last four characters), with a hash to compare them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

KIND_BANK_ACCOUNT_CHANGED = "BANK_ACCOUNT_CHANGED"
KIND_ROUND_AMOUNT = "ROUND_AMOUNT"
KINDS = (KIND_BANK_ACCOUNT_CHANGED, KIND_ROUND_AMOUNT)
SEVERITIES = ("LOW", "MEDIUM", "HIGH")
STATUS_OPEN, STATUS_CONFIRMED, STATUS_DISMISSED = "OPEN", "CONFIRMED", "DISMISSED"
STATUSES = (STATUS_OPEN, STATUS_CONFIRMED, STATUS_DISMISSED)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class PaymentRiskFlag(Base):
    __tablename__ = "payment_risk_flags"
    __table_args__ = (
        UniqueConstraint("work_item_id", "kind", name="uq_payment_risk_flags_item_kind"),
        CheckConstraint(f"kind IN ({_in(KINDS)})", name="ck_payment_risk_flags_kind"),
        CheckConstraint(f"severity IN ({_in(SEVERITIES)})", name="ck_payment_risk_flags_severity"),
        CheckConstraint(f"status IN ({_in(STATUSES)})", name="ck_payment_risk_flags_status"),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_payment_risk_flags_details_object"),
        Index("ix_payment_risk_flags_workspace_status", "workspace_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("work_items.id", ondelete="CASCADE"), nullable=False)
    counterpart_work_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("work_items.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=STATUS_OPEN)
    vendor_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
