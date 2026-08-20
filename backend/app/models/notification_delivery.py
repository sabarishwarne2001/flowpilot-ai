"""ARCH-12 Step 7 — one attempt-tracked delivery of one notification.

`notifications` answers "what happened". This answers "did it reach the user
down channel X, and if not, how many times have we tried and when do we try
again". Those are different questions and the second one has a per-channel
answer, which is why it is a second table rather than four more columns.

The retry shape is deliberately identical to `jobs`: `attempts`,
`max_attempts`, a terminal DEAD status rather than a derived
`attempts >= max_attempts`, and a due-time index that is partial over
non-terminal rows. Anything an on-call engineer already knows about draining
the job queue transfers directly.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.notification import NotificationChannel


class NotificationDeliveryStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    DEAD = "DEAD"


CLAIMABLE_DELIVERY_STATUSES: tuple[NotificationDeliveryStatus, ...] = (
    NotificationDeliveryStatus.PENDING,
    NotificationDeliveryStatus.FAILED,
)

TERMINAL_DELIVERY_STATUSES: tuple[NotificationDeliveryStatus, ...] = (
    NotificationDeliveryStatus.DELIVERED,
    NotificationDeliveryStatus.DEAD,
)

#: Base for the exponential schedule, in seconds. Attempt N waits
#: BACKOFF_BASE * 2**(N-1), capped, with full jitter applied at claim time.
BACKOFF_BASE_SECONDS: float = 30.0
BACKOFF_CAP_SECONDS: float = 3600.0


def backoff_delay(attempts: int) -> timedelta:
    """Delay before attempt number `attempts + 1`.

    Capped rather than unbounded: an endpoint that has been down for six hours
    is down, and a retry schedule that has drifted to eleven hours is
    indistinguishable from dead-lettering while still holding a row in the
    claim index.
    """
    seconds = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** max(0, attempts)))
    return timedelta(seconds=seconds)


class NotificationDelivery(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "notification_deliveries"

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint(
            "(status = 'DELIVERED'::notification_delivery_status) "
            "= (delivered_at IS NOT NULL)",
            name="delivered_at_matches_status",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_is_object"),
        Index(
            "ix_notification_deliveries_due",
            "next_attempt_at",
            postgresql_where=text(
                "status IN ('PENDING'::notification_delivery_status, "
                "'FAILED'::notification_delivery_status)"
            ),
        ),
        Index(
            "ix_notification_deliveries_dead",
            "organization_id",
            text("created_at DESC"),
            postgresql_where=text("status = 'DEAD'::notification_delivery_status"),
        ),
        Index("ix_notification_deliveries_notification", "notification_id"),
        Index(
            "uq_notification_deliveries_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notifications.id", ondelete="CASCADE"),
        nullable=False,
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

    channel: Mapped[NotificationChannel] = mapped_column(
        PGEnum(
            NotificationChannel,
            name="notification_channel",
            create_type=False,
        ),
        nullable=False,
    )

    status: Mapped[NotificationDeliveryStatus] = mapped_column(
        PGEnum(
            NotificationDeliveryStatus,
            name="notification_delivery_status",
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text("'PENDING'::notification_delivery_status"),
    )

    target: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("6")
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    notification: Mapped["object"] = relationship("Notification")

    # ------------------------------------------------------------------
    # State transitions. Kept on the model so every caller applies the
    # same rules — the dispatcher, the job handler and the sweeper all
    # need them and none of them should reimplement the arithmetic.
    # ------------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_DELIVERY_STATUSES

    def mark_delivered(self) -> None:
        self.status = NotificationDeliveryStatus.DELIVERED
        self.delivered_at = datetime.now(timezone.utc)
        self.last_error = None

    def mark_failed(self, error: str) -> None:
        """Record one failed attempt and schedule the next, or dead-letter."""
        self.attempts += 1
        self.last_error = error[:2000]
        if self.attempts >= self.max_attempts:
            self.status = NotificationDeliveryStatus.DEAD
            return
        self.status = NotificationDeliveryStatus.FAILED
        self.next_attempt_at = datetime.now(timezone.utc) + backoff_delay(self.attempts)

    def __repr__(self) -> str:
        return (
            f"<NotificationDelivery {self.channel.value if self.channel else None} "
            f"{self.status.value if self.status else None} "
            f"attempts={self.attempts}>"
        )


__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_CAP_SECONDS",
    "CLAIMABLE_DELIVERY_STATUSES",
    "NotificationDelivery",
    "NotificationDeliveryStatus",
    "TERMINAL_DELIVERY_STATUSES",
    "backoff_delay",
]