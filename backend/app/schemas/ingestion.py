"""ARCH-38 — request and response shapes for batch ingestion."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.storage import DEFAULT_PART_SIZE
from app.services.ingestion.bulk_service import BULK_ACTIONS, MAX_BULK_IDS


class BatchFileRequest(BaseModel):
    """One file the browser intends to upload."""

    client_key: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class BatchCreateRequest(BaseModel):
    source: Literal["FILES", "FOLDER", "ARCHIVE"] = "FILES"
    files: list[BatchFileRequest] = Field(default_factory=list, max_length=2000)


class BatchItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_key: str
    filename: str
    size_bytes: int
    status: str
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    work_item_id: Optional[uuid.UUID] = None
    upload_session_id: Optional[uuid.UUID] = None


class BatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    source: str
    total_items: int
    completed_items: int
    failed_items: int
    created_at: datetime
    completed_at: Optional[datetime] = None
    items: list[BatchItemResponse] = Field(default_factory=list)


class UploadSessionCreateRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: Optional[str] = Field(default=None, max_length=128)
    total_size: Optional[int] = Field(default=None, ge=0)
    sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    batch_item_id: Optional[uuid.UUID] = None

    @field_validator("filename")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("filename must not be blank")
        return cleaned


class UploadSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    part_size: int = DEFAULT_PART_SIZE
    parts_received: list[int] = Field(default_factory=list)
    status: str
    expires_at: datetime
    total_size: Optional[int] = None


class UploadSessionCompleteResponse(BaseModel):
    session_id: uuid.UUID
    work_item_id: uuid.UUID
    batch_id: Optional[uuid.UUID] = None


class BulkActionRequest(BaseModel):
    action: Literal["delete", "reprocess", "export", "tag"]
    ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_BULK_IDS)
    idempotency_key: str = Field(min_length=8, max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=24)
    export_format: Literal["csv", "json"] = "csv"

    @field_validator("action")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in BULK_ACTIONS:
            raise ValueError(f"{value!r} is not a bulk action")
        return value


class BulkItemResult(BaseModel):
    work_item_id: str
    outcome: str
    code: Optional[str] = None
    detail: Optional[str] = None


class BulkActionResponse(BaseModel):
    action: str
    succeeded: int
    refused: int
    results: list[BulkItemResult] = Field(default_factory=list)
    export_body: Optional[str] = None
    export_mime_type: Optional[str] = None


class TagListResponse(BaseModel):
    tags: list[str] = Field(default_factory=list)


class PresetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    organization_id: Optional[uuid.UUID] = None
    industry: str
    document_type: str
    version: int
    label: str
    description: str
    schema_fields: dict[str, Any] = Field(default_factory=dict)
    assertions: list[Any] = Field(default_factory=list)
    classifier_hints: list[Any] = Field(default_factory=list)
    redaction_profile: Optional[str] = None
    is_platform: bool = True
    applied: bool = False
    enabled: bool = False
    safe_harbor_notice: Optional[str] = None


class PresetApplyRequest(BaseModel):
    preset_id: uuid.UUID


class PresetEnableRequest(BaseModel):
    enabled: bool


class RetentionHoldRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=255)
    reference: Optional[str] = Field(default=None, max_length=128)
    workspace_id: Optional[uuid.UUID] = None
    work_item_id: Optional[uuid.UUID] = None


class RetentionHoldResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: Optional[uuid.UUID] = None
    work_item_id: Optional[uuid.UUID] = None
    reason: str
    reference: Optional[str] = None
    placed_at: datetime
    released_at: Optional[datetime] = None


__all__ = [
    "BatchCreateRequest",
    "BatchFileRequest",
    "BatchItemResponse",
    "BatchResponse",
    "BulkActionRequest",
    "BulkActionResponse",
    "BulkItemResult",
    "PresetApplyRequest",
    "PresetEnableRequest",
    "PresetResponse",
    "RetentionHoldRequest",
    "RetentionHoldResponse",
    "TagListResponse",
    "UploadSessionCompleteResponse",
    "UploadSessionCreateRequest",
    "UploadSessionResponse",
]
