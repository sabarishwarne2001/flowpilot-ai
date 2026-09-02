"""ARCH-09 §6d — per-attempt delivery history."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class AttemptDisposition(str, PyEnum):
    """What this attempt decided about the delivery's future."""

    DELIVERED = "DELIVERED"
    RETRY = "RETRY"
    DEAD = "DEAD"


class WebhookDeliveryAttempt(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "webhook_delivery_attempts"

    __table_args__ = (
        CheckConstraint(
            "attempt_number >= 1",
            name="attempt_number_positive",
        ),
        CheckConstraint(
            "duration_ms >= 0",
            name="duration_non_negative",
        ),
        CheckConstraint(
            "response_status IS NOT NULL OR error IS NOT NULL",
            name="outcome_recorded",
        ),
        CheckConstraint(
            "response_status IS NULL OR (response_status BETWEEN 100 AND 599)",
            name="status_in_range",
        ),
        UniqueConstraint(
            "webhook_delivery_id",
            "attempt_number",
            name="uq_webhook_delivery_attempts_delivery_attempt",
        ),
        Index(
            "ix_webhook_delivery_attempts_delivery_id_attempt",
            "webhook_delivery_id",
            text("attempt_number DESC"),
        ),
        Index("ix_webhook_delivery_attempts_attempted_at", "attempted_at"),
        Index(
            "ix_webhook_delivery_attempts_organization_id_attempted_at",
            "organization_id",
            text("attempted_at DESC"),
        ),
    )

    webhook_delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhook_deliveries.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)

    request_url: Mapped[str] = mapped_column(Text, nullable=False)
    request_headers: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        doc="REDACTED. X-FlowPilot-Signature is replaced with a marker before storage.",
    )
    resolved_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)

    response_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_headers: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    response_body_excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    disposition: Mapped[AttemptDisposition] = mapped_column(
        PGEnum(
            AttemptDisposition,
            name="webhook_attempt_disposition",
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<WebhookDeliveryAttempt delivery={self.webhook_delivery_id} "
            f"#{self.attempt_number} {self.disposition.value if self.disposition else None} "
            f"status={self.response_status}>"
        )
