"""ARCH43-S1:schemas — packet dicer API models. The console mirrors these in
frontend/src/types/packets.ts; verify_arch43 T9 compares the field lists."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SegmentRow(BaseModel):
    id: uuid.UUID
    ordinal: int
    page_start: int
    page_end: int
    document_type: Optional[str] = None
    confidence: Optional[float] = None
    title: Optional[str] = None
    child_work_item_id: Optional[uuid.UUID] = None


class PageScore(BaseModel):
    page: int
    p: float
    doc_type: str
    features: Optional[dict[str, float]] = None


class PacketSplitRow(BaseModel):
    id: uuid.UUID
    work_item_id: uuid.UUID
    original_filename: str
    status: str
    source: str
    page_count: int
    segment_count: int
    certainty: Optional[float] = None
    model_version: str
    proposed_at: datetime
    decided_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None


class PacketSplitList(BaseModel):
    items: list[PacketSplitRow]
    total: int


class PacketSplitDetail(BaseModel):
    split: PacketSplitRow
    threshold: float
    segments: list[SegmentRow]
    pages: list[PageScore]
    failure_reason: Optional[str] = None


class BoundariesRequest(BaseModel):
    boundaries: list[int] = Field(default_factory=list, max_length=2000)


class ApproveRequest(BaseModel):
    boundaries: Optional[list[int]] = Field(default=None, max_length=2000)


class LineageParent(BaseModel):
    work_item_id: uuid.UUID
    original_filename: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None


class LineageChild(BaseModel):
    ordinal: int
    page_start: int
    page_end: int
    document_type: Optional[str] = None
    work_item_id: Optional[uuid.UUID] = None
    original_filename: Optional[str] = None
    pipeline_stage: Optional[str] = None


class LineageSplit(BaseModel):
    split_id: uuid.UUID
    status: str


class WorkItemLineage(BaseModel):
    work_item_id: uuid.UUID
    parent: Optional[LineageParent] = None
    split: Optional[LineageSplit] = None
    children: list[LineageChild]


