"""
Database representation of the in-app Notification entity for FlowPilot AI.

Manages persistent alert titles, message descriptions, read/unread state,
and bidirectional mappings to Users and WorkItems.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.work_item import WorkItem


class Notification(Base, UUIDMixin, TimestampMixin):
    """
    Persistent representation of an in-app notification.

    Stores notification content, read status, and optional associations
    with processed WorkItems.
    """

    __tablename__ = "notifications"

    title: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    work_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="notifications",
    )

    work_item: Mapped["WorkItem"] = relationship(
        "WorkItem",
        back_populates="notifications",
        passive_deletes=True,
    )