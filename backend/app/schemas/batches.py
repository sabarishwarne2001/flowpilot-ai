"""Phase 1 — request and response shapes of the batch engine API."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class BatchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    work_item_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)
    ingestion_batch_id: Optional[uuid.UUID] = None

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value


class BatchUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[str] = Field(default=None, pattern="^(ACTIVE|ARCHIVED)$")


class BatchDocumentsAdd(BaseModel):
    work_item_ids: list[uuid.UUID] = Field(min_length=1, max_length=2000)


class BatchProgress(BaseModel):
    documents: int
    queued: int
    processing: int
    completed: int
    failed: int
    percent: float
    straight_through: int
    review: int
    exception: int
    mean_confidence: Optional[float] = None


class BatchSummary(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    source: str
    status: str
    ingestion_batch_id: Optional[uuid.UUID] = None
    created_by_user_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime
    dispatched_at: Optional[datetime] = None
    progress: BatchProgress


class BatchList(BaseModel):
    items: list[BatchSummary]
    total: int


class HealingChangeOut(BaseModel):
    kind: str
    field: str
    from_field: Optional[str] = None
    before: Any = None
    after: Any = None
    note: str = ""


class HealingIssueOut(BaseModel):
    kind: str
    field: str
    message: str
    value: Any = None


class BatchDocument(BaseModel):
    work_item_id: uuid.UUID
    original_filename: str
    status: str
    document_type: Optional[str] = None
    added_at: datetime
    confidence: Optional[float] = None
    verification_status: Optional[str] = None
    # The lane it goes to now (computed) and the lane it was dispatched to (recorded).
    lane: Optional[str] = None
    reasons: list[str] = []
    dispatched_lane: Optional[str] = None
    dispatched_at: Optional[datetime] = None
    schema_key: Optional[str] = None
    schema_label: Optional[str] = None
    schema_state: str
    completeness: Optional[float] = None
    changes: list[HealingChangeOut] = []
    issues: list[HealingIssueOut] = []
    failure_reason: Optional[str] = None


class DispatchPolicyOut(BaseModel):
    straight_through_min_confidence: float
    review_min_confidence: float
    require_required_fields: bool
    tag_documents: bool
    is_default: bool
    updated_at: Optional[datetime] = None


class DispatchPolicyIn(BaseModel):
    straight_through_min_confidence: Decimal = Field(ge=0, le=1)
    review_min_confidence: Decimal = Field(ge=0, le=1)
    require_required_fields: bool = True
    tag_documents: bool = True

    @model_validator(mode="after")
    def _ordered(self) -> "DispatchPolicyIn":
        if self.review_min_confidence > self.straight_through_min_confidence:
            raise ValueError("the review floor cannot be above the straight-through threshold")
        return self


class BatchDetail(BaseModel):
    batch: BatchSummary
    policy: DispatchPolicyOut
    documents: list[BatchDocument]


class DispatchResult(BaseModel):
    dispatched_at: datetime
    straight_through: int
    review: int
    exception: int
    pending: int
    tagged: bool


class HealRequest(BaseModel):
    work_item_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)


class HealOutcome(BaseModel):
    work_item_id: uuid.UUID
    outcome: str
    event_id: Optional[uuid.UUID] = None
    changes: Optional[int] = None
    code: Optional[str] = None
    message: Optional[str] = None


class HealResult(BaseModel):
    healed: int
    refused: int
    skipped: int
    results: list[HealOutcome]


class HealingEventOut(BaseModel):
    id: uuid.UUID
    work_item_id: uuid.UUID
    batch_id: Optional[uuid.UUID] = None
    schema_key: str
    changes: list[dict[str, Any]]
    applied_by_user_id: Optional[uuid.UUID] = None
    applied_at: datetime
    reverted_at: Optional[datetime] = None


class RetryResult(BaseModel):
    requeued: int
    refused: int
    results: list[dict[str, Any]]


class PackageCreate(BaseModel):
    name: str = Field(default="", max_length=160)
    batch_id: Optional[uuid.UUID] = None
    work_item_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)
    include_originals: bool = True

    @model_validator(mode="after")
    def _something(self) -> "PackageCreate":
        if self.batch_id is None and not self.work_item_ids:
            raise ValueError("name a batch or at least one document")
        return self


class PackageFileOut(BaseModel):
    path: str
    sha256: str
    bytes: int


class PackageOut(BaseModel):
    id: uuid.UUID
    name: str
    batch_id: Optional[uuid.UUID] = None
    status: str
    include_originals: bool
    document_count: int
    file_count: int
    size_bytes: int
    package_sha256: Optional[str] = None
    manifest_sha256: Optional[str] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    download_count: int
    created_by_user_id: Optional[uuid.UUID] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    last_downloaded_at: Optional[datetime] = None


class PackageList(BaseModel):
    items: list[PackageOut]
    total: int


class PackageManifestOut(BaseModel):
    package: PackageOut
    files: list[PackageFileOut]


class VerifyProblem(BaseModel):
    path: str
    expected: Optional[str] = None
    actual: Optional[str] = None
    status: str


class VerifyResult(BaseModel):
    verdict: str
    message: str
    package_id: Optional[uuid.UUID] = None
    package_name: Optional[str] = None
    issued_at: Optional[str] = None
    files_checked: int
    files_ok: int
    problems: list[VerifyProblem]
    manifest_sha256: Optional[str] = None
    recorded_manifest_sha256: Optional[str] = None
    archive_sha256: str
    archive_matches_record: Optional[bool] = None
    manifest_matches_record: Optional[bool] = None


class BatchAnalytics(BaseModel):
    """Confidence, schema health, lanes and throughput of one batch (see services/batches/service.py)."""

    documents: int
    status: dict[str, int]
    confidence: dict[str, Any]
    fields: list[dict[str, Any]]
    document_types: list[dict[str, Any]]
    lanes: dict[str, int]
    straight_through_rate: Optional[float] = None
    schema_health: dict[str, int] = Field(alias="schema")
    throughput: dict[str, Optional[float]]

    model_config = {"populate_by_name": True}
