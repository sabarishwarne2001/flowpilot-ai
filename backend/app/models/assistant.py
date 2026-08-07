"""
Database representation of Conversation Memory and Messages for FlowPilot AI.
"""

import uuid
from typing import Any, TYPE_CHECKING

from sqlalchemy import ForeignKey, JSON, String, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.core.config import settings
from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace


class Conversation(Base, UUIDMixin, TimestampMixin):
    """
    Persistent representation of an interactive AI Assistant conversation.
    """
    __tablename__ = "conversations"

    __table_args__ = (
        Index(
            "ix_conversations_workspace_user_updated",
            "workspace_id",
            "user_id",
            "updated_at",
        ),
    )

    title: Mapped[str] = mapped_column(
        String(settings.MAX_CONVERSATION_TITLE_LENGTH),
        nullable=False,
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
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

    workspace: Mapped["Workspace"] = relationship("Workspace")

    user: Mapped["User"] = relationship(
        "User",
    )

    work_item: Mapped["WorkItem"] = relationship(
        "WorkItem",
        back_populates="conversations",
    )

    messages: Mapped[list["ConversationMessage"]] = relationship(
        "ConversationMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ConversationMessage(Base, UUIDMixin, TimestampMixin):
    """
    Represents a single message inside a Conversation.
    """
    __tablename__ = "conversation_messages"

    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    token_usage: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="messages",
    )