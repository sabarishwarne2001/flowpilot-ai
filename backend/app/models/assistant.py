"""
Database representation of Conversation Memory and Messages for FlowPilot AI.

ARCH-12 Step 1 & 6 — the message row now has to be able to describe a
generation that is still happening, one that stopped early, and one whose
token counts are an estimate rather than a provider fact. See
`alembic/versions/arch12_step1_stream_state.py` for why each column exists.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, JSON, String, Index, text
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.core.config import settings
from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace


class StreamState(str, enum.Enum):
    """Lifecycle of one assistant row.

    NONE is what the synchronous path writes and what every pre-ARCH-12 row
    carries. STREAMING is the only non-terminal value; a row still holding it
    past `settings.STREAM_DEADLINE_SECONDS` is the "completed but unsettled"
    failure mode and is what the in-flight sweeper looks for.
    """

    NONE = "NONE"
    STREAMING = "STREAMING"
    COMPLETE = "COMPLETE"
    ABORTED = "ABORTED"


class FinishReason(str, enum.Enum):
    """Why a stream reached a terminal state.

    Deliberately a plain `String` column with a CHECK rather than a PostgreSQL
    enum: this vocabulary is expected to grow every time a new termination
    path is added (ARCH-13 adds at least one for tool-loop bounds), and a
    CHECK constraint can be widened inside a normal transaction while
    `ALTER TYPE ... ADD VALUE` cannot.
    """

    COMPLETED = "completed"
    CLIENT_DISCONNECTED = "client_disconnected"
    PROVIDER_ERROR = "provider_error"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    SPEND_LIMIT = "spend_limit"
    OUTPUT_CEILING = "output_ceiling"
    FILTERED = "filtered"


TERMINAL_STREAM_STATES: tuple[StreamState, ...] = (
    StreamState.COMPLETE,
    StreamState.ABORTED,
)


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
        # ARCH39-S1:conversation-model — mirrors arch39_step1_conversations.
        CheckConstraint(
            "scope_mode IN ('WORKSPACE', 'SELECTED', 'DOCUMENT')",
            name="ck_conversations_scope_mode_known",
        ),
        CheckConstraint(
            "(scope_mode = 'DOCUMENT') = (work_item_id IS NOT NULL)",
            name="ck_conversations_scope_matches_document",
        ),
        Index(
            "ix_conversations_session_list",
            "workspace_id",
            "user_id",
            "archived_at",
            "pinned_at",
            "last_message_at",
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

    # ARCH-39. The ORM default derives DOCUMENT from work_item_id, so every
    # existing create path satisfies ck_conversations_scope_matches_document
    # without being edited.
    scope_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="WORKSPACE",
        default=lambda context: (
            "DOCUMENT"
            if context.get_current_parameters().get("work_item_id") is not None
            else "WORKSPACE"
        ),
    )
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model_override: Mapped[str | None] = mapped_column(String(96), nullable=True)
    #: Maintained by trg_conversation_messages_last_at.
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    # ------------------------------------------------------------------
    # ARCH-12 Step 1 — streaming lifecycle
    # ------------------------------------------------------------------

    stream_state: Mapped[StreamState] = mapped_column(
        PGEnum(
            StreamState,
            name="conversation_stream_state",
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text("'NONE'::conversation_stream_state"),
    )

    truncated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    finish_reason: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )

    usage_estimated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    stream_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ------------------------------------------------------------------
    # ARCH-12 Step 6 — citation provenance
    # ------------------------------------------------------------------

    context_hash: Mapped[str | None] = mapped_column(
        String(71),
        nullable=True,
    )

    audit_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
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

    @property
    def is_in_flight(self) -> bool:
        return self.stream_state is StreamState.STREAMING


__all__ = [
    "Conversation",
    "ConversationMessage",
    "FinishReason",
    "StreamState",
    "TERMINAL_STREAM_STATES",
]
