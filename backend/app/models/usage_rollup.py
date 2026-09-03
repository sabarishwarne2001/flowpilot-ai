"""ARCH-14 Step 2 — the aggregation tables.

ARCH-24 adds a cost basis alongside the existing revenue figure. The two are
deliberately not symmetrical:

    cost_micros        NOT NULL  — what we charged. Always known, because we
                                   refuse to meter an event we cannot price.
    cost_basis_micros  NULL-able — what the supplier charged us. Frequently
                                   unknown, and unknown must stay unknown.

`COALESCE(cost_basis_micros, 0)` turns an unknown supplier cost into a 100%
gross margin, and somebody eventually prices an enterprise contract off that
number. The column is nullable so the mistake cannot be silent, and
`unknown_cost_basis_event_count` carries the partial-ness so a bucket that is
40% unpriced is visibly untrustworthy rather than quietly wrong.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

NIL_UUID: str = "00000000-0000-0000-0000-000000000000"
TOTAL_EVENT_TYPE: str = "*"


class RollupGranularity(str, PyEnum):
    HOUR = "HOUR"
    DAY = "DAY"
    MONTH = "MONTH"


class RollupGrain(str, PyEnum):
    DETAIL = "DETAIL"
    ORG_TOTAL = "ORG_TOTAL"


class RollupWindowStatus(str, PyEnum):
    OPEN = "OPEN"
    SEALED = "SEALED"


class UsageRollup(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "usage_rollups"

    __table_args__ = (
        CheckConstraint(
            "granularity IN ('HOUR', 'DAY', 'MONTH')", name="granularity_known"
        ),
        CheckConstraint("grain IN ('DETAIL', 'ORG_TOTAL')", name="grain_known"),
        CheckConstraint("bucket_end > bucket_start", name="bucket_ordered"),
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),
        CheckConstraint("cost_micros >= 0", name="cost_non_negative"),
        CheckConstraint("event_count >= 0", name="event_count_non_negative"),
        CheckConstraint(
            "estimated_quantity >= 0", name="estimated_quantity_non_negative"
        ),
        CheckConstraint(
            "estimated_quantity <= quantity", name="estimated_within_total"
        ),
        CheckConstraint("late_event_count >= 0", name="late_count_non_negative"),
        # ---- ARCH-24 -----------------------------------------------------
        CheckConstraint(
            "cost_basis_micros IS NULL OR cost_basis_micros >= 0",
            name="cost_basis_non_negative",
        ),
        CheckConstraint(
            "unknown_cost_basis_event_count >= 0",
            name="unknown_cost_basis_count_non_negative",
        ),
        CheckConstraint(
            "unknown_cost_basis_event_count <= event_count",
            name="unknown_cost_basis_within_events",
        ),
        CheckConstraint(
            "grain <> 'ORG_TOTAL' OR ("
            " workspace_id IS NULL AND provider IS NULL AND model IS NULL"
            " AND price_book_id IS NULL AND unit_price_micros IS NULL)",
            name="org_total_has_no_dimensions",
        ),
        CheckConstraint(
            "event_type <> '*' OR grain = 'ORG_TOTAL'",
            name="wildcard_is_org_total_only",
        ),
        Index(
            "ix_usage_rollups_spend",
            "organization_id",
            "event_type",
            "granularity",
            "bucket_start",
            postgresql_where=text("grain = 'ORG_TOTAL'"),
        ),
        Index(
            "ix_usage_rollups_org_bucket",
            "organization_id",
            "granularity",
            "bucket_start",
        ),
        Index(
            "ix_usage_rollups_workspace",
            "workspace_id",
            "granularity",
            "bucket_start",
            postgresql_where=text("workspace_id IS NOT NULL"),
        ),
        Index(
            "ix_usage_rollups_unsealed",
            "granularity",
            "bucket_start",
            postgresql_where=text("sealed_at IS NULL"),
        ),
        Index(
            "ix_usage_rollups_unknown_cost_basis",
            "organization_id",
            "granularity",
            "bucket_start",
            postgresql_where=text("unknown_cost_basis_event_count > 0"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )

    grain: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'DETAIL'")
    )
    granularity: Mapped[str] = mapped_column(String(8), nullable=False)

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    price_book_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("price_books.id", ondelete="RESTRICT"),
        nullable=True,
    )
    unit_price_micros: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 9), nullable=True
    )

    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    bucket_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(30, 6), nullable=False, server_default=text("0")
    )
    cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # ---- ARCH-24: the supplier side of the same bucket --------------------
    #
    # Sum of `usage_events.cost_basis_micros` over the events folded here that
    # actually carried one. NULL when not a single event in the bucket did.
    #
    # Forward-only by construction: `usage_rollups_seal_immutable()` refuses
    # every UPDATE on a sealed row, so buckets sealed before ARCH-24 keep NULL
    # forever. That is the intended outcome — back-writing a financial column
    # across invoiced periods is what ARCH-18 exists to forbid — but it does
    # mean a reader must expect NULL for history rather than read it as free.
    cost_basis_micros: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    unknown_cost_basis_event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    cost_basis_source_mix: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )

    estimated_quantity: Mapped[Decimal] = mapped_column(
        Numeric(30, 6), nullable=False, server_default=text("0")
    )
    estimated_cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    estimated_event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    late_event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    late_quantity: Mapped[Decimal] = mapped_column(
        Numeric(30, 6), nullable=False, server_default=text("0")
    )
    late_cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    sealed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    @property
    def measured_quantity(self) -> Decimal:
        return Decimal(self.quantity) - Decimal(self.estimated_quantity)

    @property
    def is_sealed(self) -> bool:
        return self.sealed_at is not None

    @property
    def known_cost_basis_event_count(self) -> int:
        """Events here whose supplier cost is known."""
        return int(self.event_count) - int(self.unknown_cost_basis_event_count)

    @property
    def has_cost_basis(self) -> bool:
        """True only when a real figure exists. Never confuses NULL with zero.

        Note the asymmetry with a zero basis: a BYOK bucket legitimately costs
        us nothing and reports 0 with source ZERO_BYOK. `cost_basis_micros == 0`
        is therefore a *known* cost and this returns True for it.
        """
        return self.cost_basis_micros is not None

    @property
    def cost_basis_is_complete(self) -> bool:
        """Every event in the bucket carried a basis.

        A margin computed where this is False is a lower bound on cost and so
        an upper bound on margin. A caller that cannot express that distinction
        should refuse rather than round.
        """
        return self.has_cost_basis and int(self.unknown_cost_basis_event_count) == 0

    def __repr__(self) -> str:  # pragma: no cover
        basis = (
            "basis=unknown"
            if self.cost_basis_micros is None
            else f"basis={self.cost_basis_micros}"
        )
        if self.unknown_cost_basis_event_count:
            basis += f"(+{self.unknown_cost_basis_event_count} unpriced)"
        return (
            f"<UsageRollup {self.grain}/{self.granularity} {self.event_type} "
            f"org={self.organization_id} @{self.bucket_start.isoformat()} "
            f"qty={self.quantity} cost={self.cost_micros} {basis}"
            f"{' SEALED' if self.is_sealed else ''}>"
        )


class RollupWindow(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "rollup_windows"

    __table_args__ = (
        CheckConstraint(
            "granularity IN ('HOUR', 'DAY', 'MONTH')", name="granularity_known"
        ),
        CheckConstraint("status IN ('OPEN', 'SEALED')", name="status_known"),
        CheckConstraint("bucket_end > bucket_start", name="bucket_ordered"),
        CheckConstraint(
            "(status = 'SEALED') = (sealed_at IS NOT NULL)",
            name="sealed_at_matches_status",
        ),
        Index(
            "uq_rollup_windows_period",
            "granularity",
            "bucket_start",
            unique=True,
        ),
        Index(
            "ix_rollup_windows_sealable",
            "bucket_end",
            postgresql_where=text("status = 'OPEN'"),
        ),
    )

    granularity: Mapped[str] = mapped_column(String(8), nullable=False)
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    bucket_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'OPEN'")
    )

    first_rolled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_rolled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sealed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    late_event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RollupWindow {self.granularity} @{self.bucket_start.isoformat()} "
            f"{self.status} events={self.event_count}>"
        )


__all__ = [
    "NIL_UUID",
    "TOTAL_EVENT_TYPE",
    "RollupGrain",
    "RollupGranularity",
    "RollupWindow",
    "RollupWindowStatus",
    "UsageRollup",
]