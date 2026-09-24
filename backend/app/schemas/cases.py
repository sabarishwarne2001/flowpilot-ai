"""ARCH43-S1:schemas-cases — case intelligence API models. The console mirrors
these in frontend/src/types/cases.ts; verify_arch43 compares the field lists."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class TemplateRow(BaseModel):
    id: uuid.UUID
    key: str
    version: int
    name: str
    description: str
    status: str
    assembly_key: str
    entity_kind: Optional[str] = None
    entity_role: Optional[str] = None
    required_documents: list[dict[str, Any]]
    rules: list[dict[str, Any]]
    request_ttl_hours: int
    published_at: Optional[datetime] = None


class TemplateWrite(BaseModel):
    key: str = Field(max_length=64)
    name: str = Field(max_length=160)
    description: str = Field(default="", max_length=4000)
    assembly_key: str = "MANUAL"
    entity_kind: Optional[str] = None
    entity_role: Optional[str] = None
    required_documents: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    rules: list[dict[str, Any]] = Field(default_factory=list, max_length=60)
    request_ttl_hours: int = 72


class CaseRow(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    completeness: float
    template_id: uuid.UUID
    template_key: str
    anchor_kind: str
    anchor_entity_id: Optional[uuid.UUID] = None
    anchor_batch_id: Optional[uuid.UUID] = None
    documents: int
    failed_rules: int
    created_at: datetime
    evaluated_at: Optional[datetime] = None


class CaseList(BaseModel):
    items: list[CaseRow]
    counts_by_status: dict[str, int]


class ChecklistSlot(BaseModel):
    doc_type: str
    label: str
    min_count: int
    present: int
    satisfied: bool


class CaseDocumentRow(BaseModel):
    work_item_id: uuid.UUID
    original_filename: str
    document_type: str
    source: str
    added_at: datetime


class RuleResultRow(BaseModel):
    rule_id: str
    label: str
    op: str
    outcome: str
    left_value: Optional[str] = None
    right_value: Optional[str] = None
    detail: dict[str, Any]


class RequestRow(BaseModel):
    id: uuid.UUID
    document_type: str
    recipient_label: str
    status: str
    expires_at: datetime
    used_at: Optional[datetime] = None
    fulfilled_work_item_id: Optional[uuid.UUID] = None


class CaseDetail(BaseModel):
    case: CaseRow
    template: TemplateRow
    checklist: list[ChecklistSlot]
    documents: list[CaseDocumentRow]
    rules: list[RuleResultRow]
    requests: list[RequestRow]


class CaseCreate(BaseModel):
    template_id: uuid.UUID
    title: str = Field(max_length=200)


class CaseDocumentAdd(BaseModel):
    work_item_id: uuid.UUID
    document_type: Optional[str] = Field(default=None, max_length=64)


class RequestCreate(BaseModel):
    document_type: str = Field(max_length=64)
    recipient_label: str = Field(default="", max_length=200)
    ttl_hours: Optional[int] = None


class RequestCreated(BaseModel):
    request: RequestRow
    token: str
    upload_path: str


class PublicRequestInfo(BaseModel):
    document_type: str
    case_title: str
    expires_at: datetime


class PublicUploadResult(BaseModel):
    received: bool
    document_type: str
