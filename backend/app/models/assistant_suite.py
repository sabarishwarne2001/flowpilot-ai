"""ARCH-39 — Conversational AI Suite: scope items and prompt templates.

The conversation columns ARCH-39 adds (pinned_at, archived_at, model_override,
scope_mode, last_message_at) live on `Conversation` in app/models/assistant.py.
The two tables here are new.

Both are guarded in the database as well as in `conversation_service`:

  * `conversation_scope_items` carries `workspace_id`, and a trigger refuses a
    row whose conversation or work item belongs to another workspace. A
    service check alone is one forgotten call away from a cross-tenant search.
  * `prompt_templates` names are unique per organization among live rows,
    case-insensitively, through a partial unique index.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

SCOPE_WORKSPACE = "WORKSPACE"
SCOPE_SELECTED = "SELECTED"
SCOPE_DOCUMENT = "DOCUMENT"
SCOPE_MODES: tuple[str, ...] = (SCOPE_WORKSPACE, SCOPE_SELECTED, SCOPE_DOCUMENT)

PROMPT_TEMPLATE_NAME_MAX = 80
PROMPT_TEMPLATE_BODY_MAX = 8000


class ConversationScopeItem(Base):
    """One document a SELECTED-scope conversation searches."""

    __tablename__ = "conversation_scope_items"

    __table_args__ = (
        Index("ix_conversation_scope_items_work_item", "work_item_id"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PromptTemplate(Base, UUIDMixin, TimestampMixin):
    """A reusable prompt, shared by everyone in an organization."""

    __tablename__ = "prompt_templates"

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 80",
            name="ck_prompt_templates_name_length",
        ),
        CheckConstraint(
            "char_length(body) BETWEEN 1 AND 8000",
            name="ck_prompt_templates_body_length",
        ),
        Index(
            "uq_prompt_templates_org_name_live",
            "organization_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(PROMPT_TEMPLATE_NAME_MAX), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = [
    "ConversationScopeItem",
    "PROMPT_TEMPLATE_BODY_MAX",
    "PROMPT_TEMPLATE_NAME_MAX",
    "PromptTemplate",
    "SCOPE_DOCUMENT",
    "SCOPE_MODES",
    "SCOPE_SELECTED",
    "SCOPE_WORKSPACE",
]
