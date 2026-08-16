"""ARCH-09 §B.7 — one row per logical delivery, across every retry attempt."""

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


class WebhookDeliveryStatus(str, PyEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    DEAD = "DEAD"


CLAIMABLE_DELIVERY_STATUSES: tuple[WebhookDeliveryStatus, ...] = (
    WebhookDeliveryStatus.PENDING,
    WebhookDeliveryStatus.FAILED,
)
TERMINAL_DELIVERY_STATUSES: tuple[WebhookDeliveryStatus, ...] = (
    WebhookDeliveryStatus.DELIVERED,
    WebhookDeliveryStatus.DEAD,
)


class WebhookDelivery(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "webhook_deliveries"

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="ck_webhook_deliveries_attempts_non_negative"),
        CheckConstraint(
            "(status = 'CLAIMED'::webhook_delivery_status) = (claim_expires_at IS NOT NULL)",
            name="ck_webhook_deliveries_lease_matches_status",
        ),
        CheckConstraint(
            "(status = 'DELIVERED'::webhook_delivery_status) = (delivered_at IS NOT NULL)",
            name="ck_webhook_deliveries_delivered_at_matches_status",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_webhook_deliveries_payload_is_object",
        ),
        UniqueConstraint("seq", name="uq_webhook_deliveries_seq"),
        Index(
            "ix_webhook_deliveries_claimable",
            "available_at",
            "seq",
            postgresql_where=text(
                "status IN ('PENDING'::webhook_delivery_status, "
                "'FAILED'::webhook_delivery_status)"
            ),
        ),
        Index(
            "ix_webhook_deliveries_expired_leases",
            "claim_expires_at",
            postgresql_where=text("status = 'CLAIMED'::webhook_delivery_status"),
        ),
        Index(
            "ix_webhook_deliveries_endpoint_id_created_at",
            "webhook_endpoint_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_webhook_deliveries_organization_id_created_at",
            "organization_id",
            text("created_at DESC"),
        ),
        Index(
            "uq_webhook_deliveries_outbox_event_endpoint",
            "outbox_event_id",
            "webhook_endpoint_id",
            unique=True,
            postgresql_where=text("outbox_event_id IS NOT NULL"),
        ),
    )

    seq: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False, start=1), nullable=False, unique=True
    )

    webhook_endpoint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
        nullable=False,
    )
    outbox_event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("outbox_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        PGEnum(
            WebhookDeliveryStatus,
            name="webhook_delivery_status",
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text("'PENDING'::webhook_delivery_status"),
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
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_response_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def delivery_id_header(self) -> str:
        return str(self.id)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_DELIVERY_STATUSES

    def __repr__(self) -> str:
        return (
            f"<WebhookDelivery seq={self.seq} endpoint={self.webhook_endpoint_id} "
            f"status={self.status.value if self.status else None}>"
        )