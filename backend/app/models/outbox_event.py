"""ARCH-09 §B.1 — transactional outbox model.

The outbox row is written inside the caller's transaction, alongside the
state change and the audit row. The commit is the publish; there is no window
in which the database says a thing happened and no event exists for it.

Nothing consumes this table in Step 2. Step 3 adds the claim loop, Step 4 the
webhook fan-out.
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
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class OutboxEventStatus(str, PyEnum):
    """Lifecycle of one outbox row."""

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    DEAD = "DEAD"


#: Statuses a worker may claim from.
CLAIMABLE_STATUSES: tuple[OutboxEventStatus, ...] = (
    OutboxEventStatus.PENDING,
    OutboxEventStatus.FAILED,
)

#: Terminal statuses.
TERMINAL_STATUSES: tuple[OutboxEventStatus, ...] = (
    OutboxEventStatus.PUBLISHED,
    OutboxEventStatus.DEAD,
)


class OutboxEvent(Base, UUIDMixin, TimestampMixin):
    """A domain event durably recorded in the transaction that caused it."""

    __tablename__ = "outbox_events"

    __table_args__ = (
        CheckConstraint(
            "attempts >= 0",
            name="ck_outbox_events_attempts_non_negative",
        ),
        CheckConstraint(
            "(status = 'CLAIMED'::outbox_event_status) "
            "= (claim_expires_at IS NOT NULL)",
            name="ck_outbox_events_lease_matches_status",
        ),
        CheckConstraint(
            "(status = 'PUBLISHED'::outbox_event_status) "
            "= (published_at IS NOT NULL)",
            name="ck_outbox_events_published_at_matches_status",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_outbox_events_payload_is_object",
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
    )

    seq: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=False, start=1),
        nullable=False,
        unique=True,
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

    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    audit_log_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audit_logs.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )

    status: Mapped[OutboxEventStatus] = mapped_column(
        Enum(
            OutboxEventStatus,
            name="outbox_event_status",
            native_enum=True,
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text("'PENDING'::outbox_event_status"),
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    claimed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    claim_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
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

    def __repr__(self) -> str:
        return (
            f"<OutboxEvent seq={self.seq} {self.event_type} "
            f"status={self.status.value if self.status else None} "
            f"org={self.organization_id}>"
        )