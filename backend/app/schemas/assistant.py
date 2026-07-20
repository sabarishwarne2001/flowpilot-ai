"""
Data validation and serialization schemas (Pydantic v2) for the AI Assistant.

Defines validation models for conversation sessions, chat messages,
RAG queries, and structured source citations returned by the AI Assistant.
"""

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.core.config import settings


class ConversationRole(str, Enum):
    """
    Supported participant roles inside a conversation.
    """

    USER = "user"
    ASSISTANT = "assistant"


class SourceCitation(BaseModel):
    """
    Structured metadata describing a document chunk used during RAG retrieval.
    """

    work_item_id: uuid.UUID = Field(
        ...,
        description="Primary key UUID of the source Work Item.",
    )

    original_filename: str = Field(
        ...,
        description="Original uploaded filename.",
    )

    chunk_index: int = Field(
        ...,
        ge=0,
        description="Zero-based chunk index.",
    )

    similarity_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Vector similarity score.",
    )

    page_number: int | None = Field(
        None,
        ge=1,
        description="Optional source page number.",
    )

    snippet: str = Field(
        ...,
        min_length=1,
        description="Retrieved document snippet.",
    )

    model_config = {
        "from_attributes": True
    }


class TokenUsage(BaseModel):
    """
    Standardized token usage metadata returned by every LLM provider.
    """

    provider: str
    model: str

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    estimated_cost: float


class ConversationMessageBase(BaseModel):
    """
    Base schema shared across conversation message models.
    """

    role: ConversationRole = Field(
        ...,
        description="Conversation participant role.",
    )

    content: str = Field(
        ...,
        min_length=1,
        description="Message content.",
    )

    sources: list[SourceCitation] | None = Field(
        None,
        description="Structured RAG citations associated with this message.",
    )

    token_usage: TokenUsage | None = Field(
        None,
        description="Token usage metadata for assistant messages.",
    )


class ConversationMessageCreate(BaseModel):
    """
    Request payload used when submitting a new user message.
    """

    content: str = Field(
        ...,
        min_length=1,
        description="User message.",
    )


class ConversationMessageResponse(ConversationMessageBase):
    """
    Response schema representing a stored conversation message.
    """

    id: uuid.UUID
    conversation_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {
        "from_attributes": True
    }


class ConversationBase(BaseModel):
    """
    Base schema shared across conversation models.
    """

    title: str = Field(
        ...,
        min_length=1,
        max_length=settings.MAX_CONVERSATION_TITLE_LENGTH,
        description="Conversation title.",
    )

    work_item_id: uuid.UUID | None = Field(
        None,
        description="Optional associated Work Item.",
    )


class ConversationCreate(BaseModel):
    """
    Request payload used when creating a new conversation.
    """

    title: str | None = Field(
        None,
        max_length=settings.MAX_CONVERSATION_TITLE_LENGTH,
        description="Optional custom title. Defaults to the first user message.",
    )

    work_item_id: uuid.UUID | None = Field(
        None,
        description="Optional Work Item for document assistant mode.",
    )


class ConversationUpdate(BaseModel):
    """
    Request payload used when renaming an existing conversation.
    """

    title: str = Field(
        ...,
        min_length=1,
        max_length=settings.MAX_CONVERSATION_TITLE_LENGTH,
        description="Updated conversation title.",
    )


class ConversationResponse(ConversationBase):
    """
    Response schema representing a conversation session.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    messages: list[ConversationMessageResponse] = Field(
        default_factory=list,
        description="Chronological conversation messages.",
    )

    model_config = {
        "from_attributes": True
    }


class ChatQuery(BaseModel):
    """
    Request payload submitted to the AI Assistant.
    """

    content: str = Field(
        ...,
        min_length=1,
        description="User prompt or instruction.",
    )


class ChatResponse(BaseModel):

    response: str = Field(
        ...,
        description="Generated assistant response.",
    )

    sources: list[SourceCitation] = Field(
        default_factory=list,
        description="Document citations supporting the generated response.",
    )

    token_usage: TokenUsage = Field(
        ...,
        description="Token usage metadata for the generated response.",
    )

    model_config = {
        "from_attributes": True
    }