"""ARCH47-S1:schemas — request and response models of the ERP posting API (app/api/v1/erp.py).

The console's frontend/src/types/erp.ts mirrors every response model field for
field (verify_arch47 W3 compares them).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# -- catalog -----------------------------------------------------------------------------


class PresetInfo(BaseModel):
    key: str
    label: str
    auth_modes: list[str]
    config_keys: list[str]
    objects: list[str]
    idempotency: Optional[str] = None
    probe_objects: list[str]


class FormatInfo(BaseModel):
    key: str
    label: str
    transports: list[str]
    objects: list[str]


class ErpCatalog(BaseModel):
    formats: list[FormatInfo]
    transports: list[str]
    presets: list[PresetInfo]
    ack_modes: dict[str, list[str]]
    auth_modes: dict[str, list[str]]
    object_kinds: list[str]
    object_labels: dict[str, str]
    source_kinds: list[str]
    source_labels: dict[str, str]
    states: list[str]
    state_labels: dict[str, str]
    transforms: list[str]
    header_paths: dict[str, str]
    line_paths: dict[str, str]
    language: str


# -- targets ------------------------------------------------------------------------------


class TargetCreate(_In):
    name: str = Field(min_length=1, max_length=120)
    format: str
    transport: str
    preset: str = "NONE"
    ack_mode: Optional[str] = None
    auth_mode: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)
    credential: Optional[dict[str, Any]] = None
    lookup_prefix: Optional[str] = Field(default=None, max_length=25)
    auto_post: bool = False
    auto_sources: list[str] = Field(default_factory=list)
    auto_objects: list[str] = Field(default_factory=list)
    max_attempts: Optional[int] = Field(default=None, ge=1, le=10)
    ack_timeout_hours: Optional[int] = Field(default=None, ge=1, le=720)


class TargetUpdate(_In):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    status: Optional[str] = None
    ack_mode: Optional[str] = None
    auth_mode: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    auto_post: Optional[bool] = None
    auto_sources: Optional[list[str]] = None
    auto_objects: Optional[list[str]] = None
    max_attempts: Optional[int] = Field(default=None, ge=1, le=10)
    ack_timeout_hours: Optional[int] = Field(default=None, ge=1, le=720)


class CredentialSet(_In):
    credential: dict[str, Any]


class TargetRow(BaseModel):
    id: uuid.UUID
    name: str
    format: str
    transport: str
    preset: str
    ack_mode: str
    auth_mode: str
    status: str
    host: Optional[str] = None
    credential_set: bool
    credential_fingerprint: Optional[str] = None
    credential_updated_at: Optional[datetime] = None
    auto_post: bool
    auto_post_since: Optional[datetime] = None
    auto_sources: list[str]
    auto_objects: list[str]
    max_attempts: int
    ack_timeout_hours: int
    objects: list[str]
    lookup_prefix: Optional[str] = None
    postings: int
    open_exceptions: int
    created_at: datetime
    updated_at: datetime


class TargetList(BaseModel):
    items: list[TargetRow]


class MappingRow(BaseModel):
    id: uuid.UUID
    object_kind: str
    version: int
    status: str
    spec: dict[str, Any]
    spec_sha: str
    note: Optional[str] = None
    created_at: datetime
    retired_at: Optional[datetime] = None


class TargetDetail(BaseModel):
    target: TargetRow
    config: dict[str, Any]
    mappings: list[MappingRow]
    lookup_tables: list[str]


class TestResult(BaseModel):
    ok: bool
    kind: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


# -- mappings --------------------------------------------------------------------------------


class MappingSave(_In):
    spec: dict[str, Any]
    note: Optional[str] = Field(default=None, max_length=300)


class MappingVersions(BaseModel):
    target_id: uuid.UUID
    object_kind: str
    active: Optional[MappingRow] = None
    versions: list[MappingRow]
    default_spec: dict[str, Any]


class MappingCheck(BaseModel):
    valid: bool
    problems: list[str]


# -- lookup tables ------------------------------------------------------------------------------


class LookupCreate(_In):
    name: str = Field(min_length=1, max_length=64)
    description: Optional[str] = Field(default=None, max_length=300)
    entries: dict[str, Any] = Field(default_factory=dict)


class LookupUpdate(_In):
    description: Optional[str] = Field(default=None, max_length=300)
    entries: Optional[dict[str, Any]] = None
    merge: bool = False


class LookupRow(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    entries: dict[str, str]
    entry_count: int
    revision: int
    used_by: list[str]
    updated_at: datetime


class LookupList(BaseModel):
    items: list[LookupRow]


# -- postings ----------------------------------------------------------------------------------


class PostRequest(_In):
    target_id: uuid.UUID
    source_kind: str
    source_id: uuid.UUID
    object_kinds: list[str] = Field(min_length=1, max_length=5)


class PreviewRequest(_In):
    target_id: uuid.UUID
    source_kind: str
    source_id: uuid.UUID
    object_kind: str
    spec: Optional[dict[str, Any]] = None


class ReviewAction(_In):
    note: Optional[str] = Field(default=None, max_length=500)
    reference: Optional[str] = Field(default=None, max_length=200)


class AcknowledgeRequest(_In):
    accepted: Optional[bool] = None
    reference: Optional[str] = Field(default=None, max_length=200)
    reason: Optional[str] = Field(default=None, max_length=500)
    response_text: Optional[str] = Field(default=None, max_length=1_000_000)


class PostingRow(BaseModel):
    id: uuid.UUID
    target_id: uuid.UUID
    target_name: str
    target_format: str
    target_preset: str
    object_kind: str
    source_kind: str
    source_id: uuid.UUID
    work_item_id: Optional[uuid.UUID] = None
    work_item_filename: Optional[str] = None
    origin: str
    state: str
    document_number: Optional[str] = None
    amount: Optional[str] = None
    currency: Optional[str] = None
    attempts: int
    max_attempts: int
    next_attempt_at: Optional[datetime] = None
    external_id: Optional[str] = None
    last_error: Optional[str] = None
    mapping_version: Optional[int] = None
    rendered_filename: Optional[str] = None
    content_sha: Optional[str] = None
    delivered_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    erased: bool
    created_at: datetime
    updated_at: datetime


class PostingList(BaseModel):
    items: list[PostingRow]
    total: int
    counts_by_state: dict[str, int]


class AttemptRow(BaseModel):
    seq: int
    kind: str
    outcome: str
    http_status: Optional[int] = None
    message: Optional[str] = None
    detail: dict[str, Any]
    actor_user_id: Optional[uuid.UUID] = None
    started_at: datetime


class PostingDetail(BaseModel):
    posting: PostingRow
    canonical: Optional[dict[str, Any]] = None
    mapped: Optional[dict[str, Any]] = None
    rendered_preview: Optional[str] = None
    rendered_media_type: Optional[str] = None
    rendered_size: Optional[int] = None
    ack: dict[str, Any]
    control_numbers: dict[str, Any]
    remote_path: Optional[str] = None
    idempotency_key: str
    source_label: Optional[str] = None
    source_changed: Optional[bool] = None
    attempts: list[AttemptRow]
    review_note: Optional[str] = None


class PostResult(BaseModel):
    object_kind: str
    posting_id: Optional[uuid.UUID] = None
    created: bool
    state: Optional[str] = None
    error: Optional[str] = None


class PostResponse(BaseModel):
    results: list[PostResult]


class PreviewResponse(BaseModel):
    ok: bool
    canonical: Optional[dict[str, Any]] = None
    mapped: Optional[dict[str, Any]] = None
    rendered_preview: Optional[str] = None
    rendered_media_type: Optional[str] = None
    filename: Optional[str] = None
    problems: list[str]
    notes: list[str]


class OutcomePosting(BaseModel):
    target_id: uuid.UUID
    object_kind: str
    posting_id: Optional[uuid.UUID] = None
    state: Optional[str] = None


class OutcomeRow(BaseModel):
    kind: str
    id: uuid.UUID
    label: str
    approved_at: Optional[datetime] = None
    work_item_id: Optional[uuid.UUID] = None
    documents: dict[str, str]
    objects: list[str]
    postings: list[OutcomePosting]


class OutcomeList(BaseModel):
    items: list[OutcomeRow]


__all__ = [name for name in dir() if name[0].isupper()]
