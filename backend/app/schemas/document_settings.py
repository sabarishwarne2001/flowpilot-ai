from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


# ============================================================================
# Base
# ============================================================================

class DocumentSettingsBase(BaseModel):
    """
    Shared document processing configuration.
    """

    chunk_size: int = Field(
        default=500,
        ge=100,
        le=4000,
    )

    chunk_overlap: int = Field(
        default=100,
        ge=0,
        le=1000,
    )

    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        max_length=100,
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

    user_id: UUID

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )