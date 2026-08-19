from datetime import datetime
from uuid import UUID
from typing import Union

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# Base
# ============================================================================

class DocumentSettingsBase(BaseModel):
    """
    Shared document processing configuration.
    """
    chunk_size_tokens: int = Field(
        default=220,
        ge=32,
        le=254,
        description="Chunk size target in word-piece tokens.",
    )

    chunk_overlap_pct: int = Field(
        default=10,
        ge=0,
        le=40,
        description="Overlap percentage between consecutive chunks.",
    )

    chunk_size: int = Field(
        default=500,
        ge=100,
        le=4000,
        description="Deprecated character chunk size.",
    )

    chunk_overlap: int = Field(
        default=100,
        ge=0,
        le=1000,
        description="Deprecated character chunk overlap.",
    )

    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        max_length=100,
        frozen=True,
        description="Platform-managed embedding model.",
    )

    ocr_language: str = Field(
        default="eng",
        max_length=20,
    )

    max_upload_size: int = Field(
        default=50,
        ge=1,
        le=500,
    )

    allowed_file_types: str = Field(
        default="pdf,png,jpg,jpeg",
        max_length=255,
    )

    duplicate_detection: bool = True

    automatic_classification: bool = True

    automatic_summarization: bool = False

    automatic_entity_extraction: bool = False


# ============================================================================
# Create
# ============================================================================

class DocumentSettingsCreate(DocumentSettingsBase):
    pass


# ============================================================================
# Update
# ============================================================================

class DocumentSettingsUpdate(BaseModel):
    chunk_size_tokens: int | None = Field(default=None, ge=32, le=254)

    chunk_overlap_pct: int | None = Field(default=None, ge=0, le=40)

    chunk_size: int | None = Field(default=None, ge=100, le=4000)

    chunk_overlap: int | None = Field(default=None, ge=0, le=1000)

    embedding_model: str | None = Field(default=None, max_length=100)

    ocr_language: str | None = Field(default=None, max_length=20)

    max_upload_size: int | None = Field(default=None, ge=1, le=500)

    allowed_file_types: str | None = Field(default=None, max_length=255)

    duplicate_detection: bool | None = None

    automatic_classification: bool | None = None

    automatic_summarization: bool | None = None

    automatic_entity_extraction: bool | None = None


# ============================================================================
# Response
# ============================================================================

class DocumentSettingsResponse(DocumentSettingsBase):
    id: UUID
    workspace_id: UUID
    updated_by_user_id: Union[UUID, None] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )