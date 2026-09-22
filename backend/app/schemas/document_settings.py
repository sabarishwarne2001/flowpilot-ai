import re
from datetime import datetime
from uuid import UUID
from typing import Optional, Union

from pydantic import computed_field, BaseModel, ConfigDict, Field, field_validator


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

    intent_config: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per-workspace intent keywords. Empty dict follows platform defaults.",
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

    @field_validator("intent_config")
    @classmethod
    def _validate_intents(cls, value: Optional[dict[str, list[str]]]) -> dict[str, list[str]]:
        if value is None:
            return {}
        if len(value) > 20:
            raise ValueError("at most 20 intents")
        for intent, keywords in value.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", intent):
                raise ValueError(f"invalid intent name {intent!r}")
            if not isinstance(keywords, list) or not 1 <= len(keywords) <= 50:
                raise ValueError(f"intent {intent!r} needs 1-50 keywords")
            for keyword in keywords:
                if not isinstance(keyword, str) or not 2 <= len(keyword) <= 60:
                    raise ValueError(f"invalid keyword in {intent!r}")
        return value


class DocumentSettingsCreate(DocumentSettingsBase):
    pass


class DocumentSettingsUpdate(BaseModel):
    chunk_size_tokens: int | None = Field(default=None, ge=32, le=254)
    chunk_overlap_pct: int | None = Field(default=None, ge=0, le=40)
    intent_config: Optional[dict[str, list[str]]] = None
    chunk_size: int | None = Field(default=None, ge=100, le=4000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=1000)
    embedding_model: str | None = Field(default=None, max_length=100)
    ocr_language: str | None = Field(default=None, max_length=20)
    max_upload_size: int | None = Field(default=None, ge=1, le=500)
    allowed_file_types: str | None = Field(default=None, max_length=255)
    duplicate_detection: bool | None = None

    @field_validator("allowed_file_types")
    @classmethod
    def _file_types_supported(cls, value: str | None) -> str | None:
        """HARDENING-T2:D13. Only extensions the platform accepts; never empty."""
        if value is None:
            return None
        from app.core.config import settings
        from app.services.file_validation_service import (
            parse_extensions,
            supported_extensions,
        )

        chosen = parse_extensions(value)
        supported = supported_extensions(settings.ALLOWED_MIME_TYPES)
        unknown = [e for e in chosen if e not in supported]
        if unknown:
            raise ValueError(
                f"Unsupported file type(s): {', '.join(unknown)}. "
                f"Choose from: {', '.join(supported)}."
            )
        if not chosen:
            raise ValueError("Allow at least one file type.")
        return ",".join(chosen)
    automatic_classification: bool | None = None
    automatic_summarization: bool | None = None
    automatic_entity_extraction: bool | None = None

    @field_validator("intent_config")
    @classmethod
    def _validate_intents(cls, value: Optional[dict[str, list[str]]]) -> Optional[dict[str, list[str]]]:
        if value is None:
            return None
        if len(value) > 20:
            raise ValueError("at most 20 intents")
        for intent, keywords in value.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", intent):
                raise ValueError(f"invalid intent name {intent!r}")
            if not isinstance(keywords, list) or not 1 <= len(keywords) <= 50:
                raise ValueError(f"intent {intent!r} needs 1-50 keywords")
            for keyword in keywords:
                if not isinstance(keyword, str) or not 2 <= len(keyword) <= 60:
                    raise ValueError(f"invalid keyword in {intent!r}")
        return value


class DocumentSettingsResponse(DocumentSettingsBase):
    id: UUID
    workspace_id: UUID
    updated_by_user_id: Union[UUID, None] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("allowed_file_types", mode="after")
    @classmethod
    def _report_effective_types(cls, value: str) -> str:
        """HARDENING-T2:D13. Report what uploads actually enforce, so the
        console's chips and the upload paths can never disagree."""
        from app.core.config import settings
        from app.services.file_validation_service import effective_extensions

        return ",".join(effective_extensions(value, settings.ALLOWED_MIME_TYPES))

    # HARDENING-T2:D13. What actually serves this workspace. The stored
    # embedding_model / ocr_language columns are not read by the pipeline
    # (the embedding model is platform-wide and pinned; OCR runs with one
    # process-wide language), so the console shows these instead of inputs.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def platform_embedding_model(self) -> str:
        from app.core.config import settings

        return f"sentence-transformers/{settings.EMBEDDING_MODEL_NAME}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def platform_embedding_dimension(self) -> int:
        from app.models.document_chunk import EMBEDDING_DIMENSION

        return int(EMBEDDING_DIMENSION)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def platform_ocr_language(self) -> str:
        from app.core.config import settings

        return str(settings.OCR_LANGUAGE)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def supported_file_types(self) -> list[str]:
        from app.core.config import settings
        from app.services.file_validation_service import supported_extensions

        return supported_extensions(settings.ALLOWED_MIME_TYPES)
