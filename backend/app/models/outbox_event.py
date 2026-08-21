"""ARCH-09 §B.1 — transactional outbox model.

ARCH-13 Step 13.1 adds `visibility` (F1): one table, two audiences.
ARCH-13 Step 13.2 adds `depth` / `causation_id` / `correlation_id` (A7).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class OutboxEventStatus(str, PyEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    DEAD = "DEAD"


class OutboxVisibility(str, PyEnum):
    """ARCH-13 F1. Not a PG enum — a varchar with a CHECK.

    A PG enum would need an ALTER TYPE to add a third audience later, which
    cannot run inside a transaction on older servers. Two values with a CHECK
    is the cheaper shape for something that is unlikely to grow but must not
    be wrong.
    """

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"


CLAIMABLE_STATUSES: tuple[OutboxEventStatus, ...] = (
    OutboxEventStatus.PENDING,
    OutboxEventStatus.FAILED,
)

TERMINAL_STATUSES: tuple[OutboxEventStatus, ...] = (
    OutboxEventStatus.PUBLISHED,
    OutboxEventStatus.DEAD,
)

#: ARCH-13 Step 13.2. The database ceiling, not the configured one. See
#: `settings.AUTOMATION_MAX_DEPTH` for the value the application refuses at.
HARD_DEPTH_CEILING: int = 16


class OutboxEvent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "outbox_events"

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="ck_outbox_events_attempts_non_negative"),
        CheckConstraint(
            "(status = 'CLAIMED'::outbox_event_status) = (claim_expires_at IS NOT NULL)",
            name="ck_outbox_events_lease_matches_status",
        ),
        CheckConstraint(
            "(status = 'PUBLISHED'::outbox_event_status) = (published_at IS NOT NULL)",
            name="ck_outbox_events_published_at_matches_status",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_outbox_events_payload_is_object"),
        # -- ARCH-13 Step 13.1 -------------------------------------------
        CheckConstraint(
            "visibility IN ('PUBLIC', 'INTERNAL')",
            name="ck_outbox_events_visibility_known",
        ),
        # The vocabulary CHECK itself is written by the migration, which owns
        # the event-type list. Declaring it here would duplicate that list in
        # a second place and guarantee they drift.
        # -- ARCH-13 Step 13.2 -------------------------------------------
        CheckConstraint(
            f"depth >= 0 AND depth <= {HARD_DEPTH_CEILING}",
            name="ck_outbox_events_depth_bounded",
        ),
        CheckConstraint(
            "causation_id IS NULL OR correlation_id IS NOT NULL",
            name="ck_outbox_events_causation_implies_correlation",
        ),
        CheckConstraint(
            "depth = 0 OR correlation_id IS NOT NULL",
            name="ck_outbox_events_depth_implies_correlation",
        ),
        UniqueConstraint("seq", name="uq_outbox_events_seq"),
        Index(
            "ix_outbox_events_claimable",
            "available_at",
            "seq",
            postgresql_where=text(
                "status IN ('PENDING'::outbox_event_status, "
                "'FAILED'::outbox_event_status)"
            ),
        ),
        Index(
            "ix_outbox_events_internal_claimable",
            "available_at",
            "seq",
            postgresql_where=text(
                "visibility = 'INTERNAL' AND status IN "
                "('PENDING'::outbox_event_status, 'FAILED'::outbox_event_status)"
            ),
        ),
        Index(
            "ix_outbox_events_public_claimable",
            "available_at",
            "seq",
            postgresql_where=text(
                "visibility = 'PUBLIC' AND status IN "
                "('PENDING'::outbox_event_status, 'FAILED'::outbox_event_status)"
            ),
        ),
        Index(
            "ix_outbox_events_expired_leases",
            "claim_expires_at",
            postgresql_where=text("status = 'CLAIMED'::outbox_event_status"),
        ),
        Index(
            "ix_outbox_events_organization_id_created_at",
            "organization_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_outbox_events_audit_log_id",
            "audit_log_id",
            postgresql_where=text("audit_log_id IS NOT NULL"),
        ),
        Index(
            "uq_outbox_events_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "ix_outbox_events_prunable",
            "published_at",
            postgresql_where=text("status = 'PUBLISHED'::outbox_event_status"),
        ),
        Index(
            "ix_outbox_events_correlation",
            "correlation_id",
            "seq",
            postgresql_where=text("correlation_id IS NOT NULL"),
        ),
        Index(
            "ix_outbox_events_causation",
            "causation_id",
            postgresql_where=text("causation_id IS NOT NULL"),
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
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )

    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    audit_log_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audit_logs.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )

    # ---- ARCH-13 Step 13.1: audience -----------------------------------
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default=OutboxVisibility.PUBLIC.value
    )

    # ---- ARCH-13 Step 13.2: causal chain -------------------------------
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    causation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("outbox_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    correlation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    status: Mapped[OutboxEventStatus] = mapped_column(
        PGEnum(
            OutboxEventStatus,
            name="outbox_event_status",
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text("'PENDING'::outbox_event_status"),
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    claimed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    claim_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_claimable(self) -> bool:
        return self.status in CLAIMABLE_STATUSES

    @property
    def is_internal(self) -> bool:
        return self.visibility == OutboxVisibility.INTERNAL.value

    @property
    def chain_root_id(self) -> uuid.UUID:
        """The correlation root. A root event is its own root.

        Callers thread this into the events they emit; `emit(caused_by=...)`
        does it for them. Exposed as a property so a caller reading a claimed
        event does not have to remember the `or self.id` fallback.
        """
        return self.correlation_id or self.id

    def __repr__(self) -> str:
        return (
            f"<OutboxEvent seq={self.seq} {self.event_type} "
            f"visibility={self.visibility} depth={self.depth} "
            f"status={self.status.value if self.status else None} "
            f"org={self.organization_id}>"
        )