"""
Database representation of Conversation Memory and Messages for FlowPilot AI.

Manages persistent chat session titles, chronological participant messages,
and extensible JSON-formatted citation sources used for RAG operations.
"""

import uuid
from typing import Any, TYPE_CHECKING

from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.core.config import settings
from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.work_item import WorkItem


class Conversation(Base, UUIDMixin, TimestampMixin):
    """
    Persistent representation of an interactive AI Assistant conversation.

    A Conversation may represent:

    - Global Assistant (work_item_id is NULL)
    - Document Assistant (work_item_id references a WorkItem)
    """

    __tablename__ = "conversations"

    title: Mapped[str] = mapped_column(
        String(settings.MAX_CONVERSATION_TITLE_LENGTH),
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

    user: Mapped["User"] = relationship(
        "User",
        back_populates="conversations",
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

    Stores both user and assistant messages together with structured
    RAG source citation metadata.
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