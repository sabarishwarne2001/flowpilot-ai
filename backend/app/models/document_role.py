"""ARCH-31 — the ORM mapping for `document_roles`.

The table arrived with the Step 0 migration (`arch31_step0_document_roles`)
and had no mapped class, because Step 0 introduced no code path that read it:
it shipped pure functions, one table and one entitlement key, deliberately
ahead of anything that depended on them.

Step 2's candidate discovery is the first reader. `candidates.py` issues two
indexed queries per score — the PO-reference lookup and the vendor date
window — and both are written against this mapping rather than raw SQL so
that the `workspace_id` filter every query carries is visible in one place
and greppable. ARCH-02 isolation is not something to re-derive at each call
site.

THE CONSTRAINTS ARE DECLARED HERE TOO, AND THEY ARE NOT DECORATION
==================================================================

They already exist in the database; repeating them in `__table_args__` is
what keeps `alembic revision --autogenerate` from proposing to add them. A
model that under-declares what the table has produces a migration that
"adds" a constraint already present, which fails on apply and teaches
everyone to distrust autogenerate output.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.services.procurement_matching.role_classifier import ROLE_SOURCES, ROLES


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class DocumentRole(Base, UUIDMixin, TimestampMixin):
    """What a document IS, who decided, and the header the matcher reads.

    One row per work item — `uq_document_roles_work_item`, not
    (work_item, role). A document is one thing, and two rows would let a
    re-classification land beside a human's decision rather than being
    refused by it, which makes "did a person decide?" stop having a single
    answer.
    """

    __tablename__ = "document_roles"

    __table_args__ = (
        CheckConstraint(f"role IN ({_quoted(ROLES)})", name="ck_document_roles_role"),
        CheckConstraint(
            f"role_source IN ({_quoted(ROLE_SOURCES)})",
            name="ck_document_roles_role_source",
        ),
        CheckConstraint(
            "role_confidence IS NULL OR "
            "(role_confidence >= 0 AND role_confidence <= 1)",
            name="ck_document_roles_confidence_range",
        ),
        CheckConstraint(
            "role_source <> 'USER' OR role_confidence IS NULL",
            name="ck_document_roles_user_has_no_confidence",
        ),
        CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name="ck_document_roles_currency_shape",
        ),
        UniqueConstraint("work_item_id", name="uq_document_roles_work_item"),
        Index(
            "ix_document_roles_candidate_window",
            "workspace_id",
            "vendor_key",
            "document_date",
            postgresql_where=text("vendor_key IS NOT NULL"),
        ),
        Index(
            "ix_document_roles_number",
            "workspace_id",
            "document_number",
            postgresql_where=text("document_number IS NOT NULL"),
        ),
        Index("ix_document_roles_role", "workspace_id", "role"),
        Index("ix_document_roles_organization", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(String(32), nullable=False)
    role_source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'CLASSIFIER'")
    )
    role_confidence: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(4, 3), nullable=True
    )

    vendor_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    document_number: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    document_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    total_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    normalization_warnings: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    work_item: Mapped["Any"] = relationship("WorkItem", lazy="raise")

    @property
    def is_user_set(self) -> bool:
        """A human decided. `role_classifier.assert_not_user_locked` refuses to overwrite."""
        return self.role_source == "USER"


__all__ = ["DocumentRole"]