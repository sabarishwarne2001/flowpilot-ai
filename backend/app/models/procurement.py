"""ARCH-31 — procurement three-way matching: cases, case lines, tolerance policies.

NOT `app/models/reconciliation.py`. That file is ARCH-18 supplier COGS and
matches provider invoices against our own metered usage. This one matches a
TENANT's purchase order against their goods receipt against their supplier's
invoice. The vocabulary overlaps ("invoice", "line", "variance") and the
problems do not, which is exactly why the namespaces are kept apart.

WHY THE STATUS AND OUTCOME VALUES ARE STRINGS AND NOT PgEnum
============================================================

Every enum in this schema that used PgEnum (audit_action, slo_unit, ...) now
needs an out-of-transaction `ALTER TYPE ... ADD VALUE` migration to gain a
value, and ARCH-22's migration header records the sharp edge that follows: a
newly added value cannot be used in the transaction that added it.

These two vocabularies are governed by a CHECK constraint instead. The set is
closed by design — a seventh line outcome would change what the comparison
grid renders and what the approve rule counts as a red line, so it is a code
change either way, and a CHECK gives the same database-level refusal without
the migration hazard.

The constants below are the single source of truth; `verify_arch31.py` asserts
they equal the CHECK constraint bodies in the migration, so a value added here
and not there fails a gate rather than a production insert.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

# ---------------------------------------------------------------------------
# Vocabulary
#
# Re-exported from app/services/procurement_matching/vocabulary.py rather
# than declared here. That module imports nothing but the standard library,
# which is what lets matcher.py and verify_arch31.py's offline gates use the
# same constants without pulling the ORM registry. See its header.
# ---------------------------------------------------------------------------

from app.services.procurement_matching.vocabulary import (  # noqa: E402
    CASE_STATUS_APPROVED,
    CASE_STATUS_DISPUTED,
    CASE_STATUS_MATCHED,
    CASE_STATUS_NEEDS_REVIEW,
    CASE_STATUS_OPEN,
    CASE_STATUS_SUPERSEDED,
    CASE_STATUSES,
    COST_SCALE,
    DEFAULT_POLICY_VERSION,
    LINE_OUTCOMES,
    LIVE_CASE_STATUSES,
    OUTCOME_MATCHED,
    OUTCOME_NOT_INVOICED,
    OUTCOME_NOT_ORDERED,
    OUTCOME_NOT_RECEIVED,
    OUTCOME_PRICE_VARIANCE,
    OUTCOME_QUANTITY_VARIANCE,
    POLICY_STATUS_DRAFT,
    POLICY_STATUS_PUBLISHED,
    POLICY_STATUSES,
    RED_OUTCOMES,
    RESOLVED_CASE_STATUSES,
)


class ProcurementTolerancePolicy(Base, UUIDMixin, TimestampMixin):
    """What counts as a variance, versioned and immutable once published.

    The immutability is a database trigger
    (`trg_procurement_tolerance_policies_immutable`), not a service rule. See
    the migration header: `procurement_cases.policy_version` is a text stamp,
    and if the row behind the stamp can change then every historical case
    silently re-interprets itself.
    """

    __tablename__ = "procurement_tolerance_policies"

    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED')",
            name="ck_procurement_tolerance_policies_status",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_procurement_tolerance_policies_version_positive",
        ),
        CheckConstraint(
            "price_tolerance_micros >= 0 AND price_tolerance_bps >= 0 "
            "AND quantity_tolerance >= 0 AND candidate_window_days >= 0",
            name="ck_procurement_tolerance_policies_non_negative",
        ),
        CheckConstraint(
            "max_pair_cost > 0 AND max_pair_cost < 1000000",
            name="ck_procurement_tolerance_policies_pair_cost_range",
        ),
        CheckConstraint(
            "status <> 'PUBLISHED' OR published_at IS NOT NULL",
            name="ck_procurement_tolerance_policies_published_has_timestamp",
        ),
        Index(
            "uq_procurement_tolerance_policies_one_draft",
            "workspace_id",
            unique=True,
            postgresql_where=text("status = 'DRAFT'"),
        ),
        Index(
            "ix_procurement_tolerance_policies_current",
            "workspace_id",
            "version",
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
        Index("ix_procurement_tolerance_policies_organization", "organization_id"),
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

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'DRAFT'")
    )

    price_tolerance_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    price_tolerance_bps: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    quantity_tolerance: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, server_default=text("0")
    )
    max_pair_cost: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("600000")
    )
    candidate_window_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("90")
    )

    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    @property
    def is_published(self) -> bool:
        return self.status == POLICY_STATUS_PUBLISHED

    @property
    def policy_version(self) -> str:
        """The text stamp a case records. Never reused: version is monotonic."""
        return f"{self.id}:{self.version}"


class ProcurementCase(Base, UUIDMixin, TimestampMixin):
    """One three-way (or two-way) match between a tenant's own documents."""

    __tablename__ = "procurement_cases"

    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN', 'MATCHED', 'NEEDS_REVIEW', 'APPROVED', "
            "'DISPUTED', 'SUPERSEDED')",
            name="ck_procurement_cases_status",
        ),
        CheckConstraint(
            "(CASE WHEN po_work_item_id IS NOT NULL THEN 1 ELSE 0 END + "
            "CASE WHEN receipt_work_item_id IS NOT NULL THEN 1 ELSE 0 END + "
            "CASE WHEN invoice_work_item_id IS NOT NULL THEN 1 ELSE 0 END) >= 2",
            name="ck_procurement_cases_two_sided",
        ),
        CheckConstraint(
            "line_count >= 0 AND exception_count >= 0 "
            "AND exception_count <= line_count",
            name="ck_procurement_cases_counts_non_negative",
        ),
        CheckConstraint(
            "input_digest ~ '^[0-9a-f]{64}$'",
            name="ck_procurement_cases_digest_shape",
        ),
        CheckConstraint(
            "status NOT IN ('APPROVED', 'DISPUTED') OR resolved_at IS NOT NULL",
            name="ck_procurement_cases_resolution_recorded",
        ),
        Index(
            "uq_procurement_cases_live_invoice",
            "workspace_id",
            "invoice_work_item_id",
            unique=True,
            postgresql_where=text(
                "invoice_work_item_id IS NOT NULL AND status <> 'SUPERSEDED'"
            ),
        ),
        Index(
            "ix_procurement_cases_queue",
            "workspace_id",
            "status",
            text("created_at DESC"),
        ),
        Index("ix_procurement_cases_digest", "workspace_id", "input_digest"),
        Index("ix_procurement_cases_organization", "organization_id"),
        Index(
            "ix_procurement_cases_vendor",
            "workspace_id",
            "vendor_key",
            postgresql_where=text("vendor_key IS NOT NULL"),
        ),
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

    po_work_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=True,
    )
    receipt_work_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=True,
    )
    invoice_work_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'OPEN'")
    )
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    vendor_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    header_findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    line_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    exception_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    variance_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolution_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    lines: Mapped[list["ProcurementCaseLine"]] = relationship(
        "ProcurementCaseLine",
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="ProcurementCaseLine.line_number",
    )

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_CASE_STATUSES

    @property
    def has_red_lines(self) -> bool:
        """Whether approving this case requires a written override reason."""
        return self.exception_count > 0


class ProcurementCaseLine(Base, UUIDMixin, TimestampMixin):
    """One row of the three-way comparison grid: PO | Goods Receipt | Invoice."""

    __tablename__ = "procurement_case_lines"

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('MATCHED', 'PRICE_VARIANCE', 'QUANTITY_VARIANCE', "
            "'NOT_RECEIVED', 'NOT_ORDERED', 'NOT_INVOICED')",
            name="ck_procurement_case_lines_outcome",
        ),
        CheckConstraint(
            "pair_cost IS NULL OR (pair_cost >= 0 AND pair_cost <= 1000000)",
            name="ck_procurement_case_lines_pair_cost_range",
        ),
        CheckConstraint(
            "(outcome <> 'NOT_ORDERED' OR po_line_index IS NULL) AND "
            "(outcome <> 'NOT_INVOICED' OR invoice_line_index IS NULL) AND "
            "(outcome <> 'NOT_RECEIVED' OR receipt_line_index IS NULL) AND "
            "(outcome <> 'MATCHED' OR invoice_line_index IS NOT NULL)",
            name="ck_procurement_case_lines_outcome_matches_sides",
        ),
        CheckConstraint(
            "po_line_index IS NOT NULL OR receipt_line_index IS NOT NULL "
            "OR invoice_line_index IS NOT NULL",
            name="ck_procurement_case_lines_has_a_side",
        ),
        Index("ix_procurement_case_lines_case", "case_id", "line_number"),
        Index(
            "ix_procurement_case_lines_exceptions",
            "workspace_id",
            "outcome",
            postgresql_where=text("outcome <> 'MATCHED'"),
        ),
        Index("ix_procurement_case_lines_organization", "organization_id"),
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
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("procurement_cases.id", ondelete="CASCADE"),
        nullable=False,
    )

    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sku: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    po_line_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    po_quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    po_unit_price_micros: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    po_amount_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    receipt_line_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    receipt_quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )

    invoice_line_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    invoice_quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    invoice_unit_price_micros: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    invoice_amount_micros: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )

    pair_cost: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_delta_micros: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    quantity_delta: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )

    findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    case: Mapped["ProcurementCase"] = relationship(
        "ProcurementCase", back_populates="lines"
    )

    @property
    def is_red(self) -> bool:
        return self.outcome in RED_OUTCOMES


__all__ = [
    "ProcurementCase",
    "ProcurementCaseLine",
    "ProcurementTolerancePolicy",
    "CASE_STATUSES",
    "CASE_STATUS_OPEN",
    "CASE_STATUS_MATCHED",
    "CASE_STATUS_NEEDS_REVIEW",
    "CASE_STATUS_APPROVED",
    "CASE_STATUS_DISPUTED",
    "CASE_STATUS_SUPERSEDED",
    "LIVE_CASE_STATUSES",
    "RESOLVED_CASE_STATUSES",
    "LINE_OUTCOMES",
    "OUTCOME_MATCHED",
    "OUTCOME_PRICE_VARIANCE",
    "OUTCOME_QUANTITY_VARIANCE",
    "OUTCOME_NOT_RECEIVED",
    "OUTCOME_NOT_ORDERED",
    "OUTCOME_NOT_INVOICED",
    "RED_OUTCOMES",
    "POLICY_STATUSES",
    "POLICY_STATUS_DRAFT",
    "POLICY_STATUS_PUBLISHED",
    "COST_SCALE",
    "DEFAULT_POLICY_VERSION",
]