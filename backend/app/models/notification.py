from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING, Union

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Enum as SQLEnum, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace


class NotificationType(str, enum.Enum):
    DOCUMENT = "DOCUMENT"
    AUTOMATION = "AUTOMATION"
    EMAIL = "EMAIL"
    SYSTEM = "SYSTEM"
    SECURITY = "SECURITY"


class NotificationChannel(str, enum.Enum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    SLACK = "SLACK"
    TEAMS = "TEAMS"
    WEBHOOK = "WEBHOOK"


class NotificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class NotificationPriority(str, enum.Enum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Notification(Base, UUIDMixin, TimestampMixin):
    """
    Persistent notification record.
    """
    __tablename__ = "notifications"

    __table_args__ = (
        Index(
            "ix_notifications_workspace_user_read_created",
            "workspace_id",
            "user_id",
            "is_read",
            "created_at",
        ),
    )

    title: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    notification_type: Mapped[NotificationType] = mapped_column(
        SQLEnum(
            NotificationType,
            name="notification_type",
            create_type=False,
        ),
        nullable=False,
        default=NotificationType.SYSTEM,
        index=True,
    )

    priority: Mapped[NotificationPriority] = mapped_column(
        SQLEnum(
            NotificationPriority,
            name="notification_priority",
            create_type=False,
        ),
        nullable=False,
        default=NotificationPriority.INFO,
        index=True,
    )

    delivery_channel: Mapped[NotificationChannel] = mapped_column(
        SQLEnum(
            NotificationChannel,
            name="notification_channel",
            create_type=False,
        ),
        nullable=False,
        default=NotificationChannel.IN_APP,
        index=True,
    )

    delivery_status: Mapped[NotificationStatus] = mapped_column(
        SQLEnum(
            NotificationStatus,
            name="notification_status",
            create_type=False,
        ),
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

    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

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

    workspace: Mapped["Workspace"] = relationship("Workspace")

    user: Mapped["User"] = relationship(
        "User",
    )

    work_item: Mapped["WorkItem"] = relationship(
        "WorkItem",
        back_populates="notifications",
        passive_deletes=True,
    )