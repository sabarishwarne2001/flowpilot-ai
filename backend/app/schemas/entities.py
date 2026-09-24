"""ARCH42-S1:schemas — entity graph API shapes. Mirrored field for field by
frontend/src/types/entities.ts (verify_arch42 gate F2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class EntityPotential(BaseModel):
    documents: int


class ModelRow(BaseModel):
    kind: str
    version: Optional[int] = None
    source: str
    pair_count: int = 0
    converged: bool = False
    fitted_at: Optional[datetime] = None


class EntitySummary(BaseModel):
    counts_by_kind: dict[str, int]
    records: int
    documents_linked: int
    open_reviews: int
    open_conflicts: int
    models: list[ModelRow]


class EntityRow(BaseModel):
    id: uuid.UUID
    kind: str
    display_name: str
    documents: int
    mentions: int
    identifier_kinds: list[str]
    merged_records: int
    last_seen_at: Optional[datetime] = None


class EntityList(BaseModel):
    items: list[EntityRow]
    total: int
    page: int
    page_size: int
    matched_by_identifier: bool = False


class IdentifierRow(BaseModel):
    id: uuid.UUID
    kind: str
    display: str
    derived: bool
    entity_id: uuid.UUID


class DocumentRow(BaseModel):
    mention_id: uuid.UUID
    work_item_id: uuid.UUID
    filename: str
    role: str
    field_path: str
    decision: str
    method: str
    probability: Optional[float] = None
    entity_id: uuid.UUID
    created_at: datetime


class RelationshipRow(BaseModel):
    relation: str
    direction: str
    other_id: uuid.UUID
    other_kind: str
    other_name: str
    documents: int


class MemberRow(BaseModel):
    id: uuid.UUID
    display_name: str
    merged_at: Optional[datetime] = None
    merge_reason: Optional[str] = None
    mentions: int


class CandidateRow(BaseModel):
    id: uuid.UUID
    left_id: uuid.UUID
    left_name: str
    right_id: uuid.UUID
    right_name: str
    kind: str
    reason: str
    probability: float
    weight: float
    conflict_kinds: list[str]
    status: str
    comparison: dict[str, Optional[int]] = Field(default_factory=dict)
    created_at: datetime


class ObligationsPlaceholder(BaseModel):
    available: bool = False
    milestone: str
    items: list[dict] = Field(default_factory=list)


class Entity360(BaseModel):
    entity: EntityRow
    first_seen_at: Optional[datetime] = None
    split_from_id: Optional[uuid.UUID] = None
    identifiers: list[IdentifierRow]
    documents: list[DocumentRow]
    relationships: list[RelationshipRow]
    members: list[MemberRow]
    open_candidates: list[CandidateRow]
    obligations: ObligationsPlaceholder


class GraphNode(BaseModel):
    id: uuid.UUID
    kind: str
    label: str
    documents: int
    depth: int


class GraphEdge(BaseModel):
    source: uuid.UUID
    target: uuid.UUID
    relation: str
    weight: int


class EntityGraph(BaseModel):
    root_id: uuid.UUID
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool


class MergeRequest(BaseModel):
    into_entity_id: uuid.UUID


class SplitRequest(BaseModel):
    mention_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class RenameRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=300)


class EntityChip(BaseModel):
    entity_id: uuid.UUID
    kind: str
    display_name: str
    role: str
    decision: str
    field_path: str


class WorkItemEntities(BaseModel):
    chips: list[EntityChip]
    resolved: bool


class ResolveResult(BaseModel):
    resolved: bool
    detail: dict[str, int | bool | str] = Field(default_factory=dict)
