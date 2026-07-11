"""
Database representation of the Notification entity for FlowPilot AI.

Stores both in-app and external notification records while tracking
delivery status, retry attempts, priority, and associated resources.

This schema is intentionally extensible to support future notification
providers such as Email, Slack, Microsoft Teams, Webhooks, SMS,
WhatsApp, and Push Notifications without requiring structural redesign.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship
from sqlalchemy.types import UUID

from app.db.base import Base
from app.db.base import TimestampMixin
from app.db.base import UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.work_item import WorkItem


# ============================================================================
# Notification Type
# ============================================================================

class NotificationType(str, enum.Enum):
    """
    High-level category describing why the notification exists.
    """

    DOCUMENT = "DOCUMENT"
    AUTOMATION = "AUTOMATION"
    EMAIL = "EMAIL"
    SYSTEM = "SYSTEM"
    SECURITY = "SECURITY"


# ============================================================================
# Delivery Channel
# ============================================================================

class NotificationChannel(str, enum.Enum):
    """
    Destination through which the notification is delivered.
    """

    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    SLACK = "SLACK"
    TEAMS = "TEAMS"
    WEBHOOK = "WEBHOOK"


# ============================================================================
# Delivery Status
# ============================================================================

class NotificationStatus(str, enum.Enum):
    """
    Current delivery state.
    """

    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


# ============================================================================
# Notification Priority
# ============================================================================

class NotificationPriority(str, enum.Enum):
    """
    Indicates how important a notification is to the recipient.
    """

    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"


# ============================================================================
# Notification Model
# ============================================================================

class Notification(Base, UUIDMixin, TimestampMixin):
    """
    Persistent notification record.

    A notification belongs to exactly one user and may optionally
    reference a WorkItem.

    Notifications support multiple delivery channels while tracking
    delivery state, retries, and failures.
    """

    __tablename__ = "notifications"

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------

    title: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    notification_type: Mapped[NotificationType] = mapped_column(
        SQLEnum(NotificationType),
        nullable=False,
        default=NotificationType.SYSTEM,
        index=True,
    )

    priority: Mapped[NotificationPriority] = mapped_column(
        SQLEnum(NotificationPriority),
        nullable=False,
        default=NotificationPriority.INFO,
        index=True,
    )

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    delivery_channel: Mapped[NotificationChannel] = mapped_column(
        SQLEnum(NotificationChannel),
        nullable=False,
        default=NotificationChannel.IN_APP,
        index=True,
    )

    delivery_status: Mapped[NotificationStatus] = mapped_column(
        SQLEnum(NotificationStatus),
        nullable=False,
        default=NotificationStatus.PENDING,
        index=True,
    )

    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    failure_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # ------------------------------------------------------------------
    # Read State
    # ------------------------------------------------------------------

    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    work_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "work_items.id",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    user: Mapped["User"] = relationship(
        "User",
        back_populates="notifications",
    )

    work_item: Mapped["WorkItem"] = relationship(
        "WorkItem",
        back_populates="notifications",
        passive_deletes=True,
    )