"""ARCH-10 Step 2 — the metering ledger.

One row per billable occurrence, written in the same transaction as the work
it measures. This table is financial evidence: it is append-only, and the only
column any later phase may update is `aggregated_at` (ARCH-14 rollups). The
migration installs a trigger enforcing exactly that.

Design notes worth keeping:

- `occurred_at` is separate from `created_at`. A worker recording usage for a
  document it processed 40 seconds ago must bill the occurrence, not the
  write. Every aggregation query uses `occurred_at`.
- `idempotency_key` with a partial unique index is the column that makes
  retries safe. Jobs retry by design; without this, a lease expiry double-bills
  the tenant. Handlers derive it deterministically, e.g. f"ocr:{job_id}:{page}".
- `quantity` is NUMERIC, not INTEGER, because `storage.gb_month` is fractional.
- `cost_micros` is nullable because self-hosted PaddleOCR has no per-page
  provider cost. Spend controls therefore cap on quantity *and* cost, not cost
  alone — see ARCH-10 Step 3.
- Attribution mirrors `audit_logs`: at most one of `actor_id`/`api_key_id`,
  with SYSTEM expressed as neither set plus `details.principal = 'SYSTEM'`.
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
    Identity,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


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
        # The read pattern for both the Step 3 spend check and ARCH-14 rollups.
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
        # ARCH-14 claims unaggregated rows through this index.
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
        # The retry-safety guarantee.
        Index(
            "uq_usage_events_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
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

    #: Estimated provider cost in millionths of a USD. NULL means "no external
    #: cost was incurred", which is different from "cost was zero and known".
    cost_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

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
    #: Set by ARCH-14 when this row has been folded into a rollup. The only
    #: column the immutability trigger permits an UPDATE to touch.
    aggregated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<UsageEvent seq={self.seq} {self.event_type} "
            f"qty={self.quantity} org={self.organization_id}>"
        )