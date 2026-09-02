"""ARCH-10 Step 2 & ARCH-14 Step 1b — the metering ledger with self-describing price columns."""

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
    Identity,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.supplier_cogs import COST_BASIS_SOURCE_VALUES

_COST_BASIS_SOURCE_IN = ", ".join(f"'{v}'" for v in COST_BASIS_SOURCE_VALUES)


class UsageEvent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "usage_events"

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "cost_micros IS NULL OR cost_micros >= 0", name="cost_non_negative"
        ),
        CheckConstraint(
            "num_nonnulls(actor_id, api_key_id) <= 1",
            name="single_principal",
        ),
        CheckConstraint(
            "details IS NULL OR jsonb_typeof(details) = 'object'",
            name="details_is_object",
        ),
        CheckConstraint("length(event_type) > 0", name="event_type_not_blank"),
        CheckConstraint(
            "(price_book_id IS NULL) = (unit_price_micros IS NULL)",
            name="price_pair_complete",
        ),
        CheckConstraint(
            "unit_price_micros IS NULL OR cost_micros IS NULL "
            "OR cost_micros = round(quantity * unit_price_micros)",
            name="cost_matches_unit_price",
        ),
        Index(
            "ix_usage_events_org_type_occurred_at",
            "organization_id",
            "event_type",
            "occurred_at",
        ),
        Index(
            "ix_usage_events_org_occurred_at",
            "organization_id",
            text("occurred_at DESC"),
        ),
        Index(
            "ix_usage_events_workspace_occurred_at",
            "workspace_id",
            text("occurred_at DESC"),
            postgresql_where=text("workspace_id IS NOT NULL"),
        ),
        Index(
            "ix_usage_events_unaggregated",
            "occurred_at",
            "seq",
            postgresql_where=text("aggregated_at IS NULL"),
        ),
        Index(
            "ix_usage_events_job_id",
            "job_id",
            postgresql_where=text("job_id IS NOT NULL"),
        ),
        Index(
            "uq_usage_events_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "ix_usage_events_unpriced",
            "occurred_at",
            postgresql_where=text("price_book_id IS NULL"),
        ),
        # ---- ARCH-18: the cost basis --------------------------------------
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
            "ix_usage_events_provider_cost_basis",
            "provider",
            "occurred_at",
            postgresql_where=text("provider IS NOT NULL"),
        ),
        Index(
            "ix_usage_events_unknown_cost_basis",
            "occurred_at",
            postgresql_where=text("cost_basis_micros IS NULL"),
        ),
    )

    seq: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False, start=1), nullable=False, unique=True
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

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    cost_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    price_book_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("price_books.id", ondelete="RESTRICT"),
        nullable=True,
    )
    unit_price_micros: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 9), nullable=True
    )

    # ---- ARCH-18 ----------------------------------------------------------
    # Denormalised at settle time for exactly the reason unit_price_micros is:
    # a price book published next quarter must not restate what last quarter
    # cost. usage_events_immutable() (V3) enumerates both of these columns, so
    # a later UPDATE is refused at the database rather than merely discouraged.
    #
    # NULL means unknown and must render as unknown. It is never coerced to 0
    # anywhere in the read path — a silent zero reads as 100% gross margin,
    # which is the single most misleading number this phase could produce.
    cost_basis_micros: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    cost_basis_source: Mapped[Optional[str]] = mapped_column(
        String(24), nullable=True
    )

    resource_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    idempotency_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    aggregated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<UsageEvent seq={self.seq} {self.event_type} "
            f"qty={self.quantity} org={self.organization_id}>"
        )
