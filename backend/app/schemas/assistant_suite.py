"""ARCH-39 — request and response shapes for conversation sessions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConversationSessionSummary(BaseModel):
    id: uuid.UUID
    title: str
    kind: Literal["workspace", "document"]
    scope_mode: Literal["WORKSPACE", "SELECTED", "DOCUMENT"]
    work_item_id: Optional[uuid.UUID] = None
    document_title: Optional[str] = None
    scope_document_count: int = 0
    model_override: Optional[str] = None
    pinned: bool = False
    archived: bool = False
    message_count: int = 0
    created_at: datetime
    last_message_at: Optional[datetime] = None


class ConversationSessionUpdate(BaseModel):
    """Every field optional; only the ones present change."""

    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    pinned: Optional[bool] = None
    archived: Optional[bool] = None
    model_override: Optional[str] = Field(default=None, max_length=96)
    clear_model_override: bool = False


class ConversationScopeRead(BaseModel):
    mode: Literal["WORKSPACE", "SELECTED", "DOCUMENT"]
    work_item_ids: list[uuid.UUID] = Field(default_factory=list)
    max_documents: int


class ConversationScopeUpdate(BaseModel):
    mode: Literal["WORKSPACE", "SELECTED"]
    work_item_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class AssistantModelOption(BaseModel):
    provider: str
    model: str
    is_workspace_default: bool
    input_micros_per_million: Optional[int] = None
    output_micros_per_million: Optional[int] = None
    currency: Optional[str] = None


class PromptTemplateRead(BaseModel):
    id: uuid.UUID
    name: str
    body: str
    created_by_user_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PromptTemplateWrite(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=8000)


__all__ = [
    "AssistantModelOption",
    "ConversationScopeRead",
    "ConversationScopeUpdate",
    "ConversationSessionSummary",
    "ConversationSessionUpdate",
    "PromptTemplateRead",
    "PromptTemplateWrite",
]
