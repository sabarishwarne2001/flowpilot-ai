"""ARCH45-S1:schemas — the corroborator's wire contract. frontend/src/types/corroboration.ts
mirrors these field lists; verify_arch45 T9 compares them."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class CorroborationRequest(BaseModel):
    work_item_ids: list[uuid.UUID] = Field(min_length=2, max_length=5)
    rules: list[str] = Field(default_factory=list, max_length=25)
    use_workspace_rules: bool = True
    materiality_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    money_tolerance: Optional[float] = Field(default=None, ge=0)
    relative_tolerance: Optional[float] = Field(default=None, ge=0, le=0.5)
    layers: Optional[list[str]] = None
    force: bool = False

    @field_validator("layers")
    @classmethod
    def _known_layers(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        known = ("FIELD", "ENTITY", "CLAUSE", "TABLE", "RULE")
        upper = [x.strip().upper() for x in value]
        bad = [x for x in upper if x not in known]
        if bad or not upper:
            raise ValueError(f"layers are some of {', '.join(known)}")
        return upper


class RunDocumentBrief(BaseModel):
    work_item_id: uuid.UUID
    position: int
    label: str


class RunSummary(BaseModel):
    id: uuid.UUID
    status: str
    document_count: int
    discrepancy_count: int
    material_count: int
    open_material_count: int
    max_materiality: float
    encoder: str
    engine_version: str
    fingerprint: str
    set_hash: str
    error: Optional[str] = None
    created_by_user_id: Optional[uuid.UUID] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    stale_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    documents: list[RunDocumentBrief]


class RunList(BaseModel):
    items: list[RunSummary]
    total: int
    counts_by_status: dict[str, int]


class PageGeometry(BaseModel):
    page: int
    width: int
    height: int


class RunDocument(BaseModel):
    work_item_id: uuid.UUID
    position: int
    label: str
    page_count: Optional[int] = None
    content_hash: str
    file_type: str
    renderable: bool
    changed_since: bool
    pages: list[PageGeometry]


class DiscrepancyRow(BaseModel):
    id: uuid.UUID
    ordinal: int
    layer: str
    kind: str
    group_key: str
    label: str
    summary: str
    materiality: float
    severity: str
    is_material: bool
    values: dict[str, Any]
    evidence: list[Any]
    detail: dict[str, Any]
    status: str
    decided_at: Optional[datetime] = None
    decided_by_user_id: Optional[uuid.UUID] = None
    note: Optional[str] = None


class PairRow(BaseModel):
    left_work_item_id: uuid.UUID
    right_work_item_id: uuid.UUID
    clauses_left: int
    clauses_right: int
    clauses_matched: int
    clauses_identical: int
    clause_similarity: float
    fields_compared: int
    fields_agreeing: int
    lines_left: int
    lines_right: int
    lines_matched: int
    lines_agreeing: int
    entities_compared: int
    entities_agreeing: int
    discrepancy_count: int
    material_count: int
    agreement: float
    alignment: list[Any]


class RuleRow(BaseModel):
    key: str
    sentence: str
    source: str
    family: str
    understood_as: str
    digest: str
    definition_id: Optional[str] = None


class RunDetail(BaseModel):
    run: RunSummary
    stale: bool
    options: dict[str, Any]
    layers: dict[str, Any]
    stats: dict[str, Any]
    anchors: list[Any]
    documents: list[RunDocument]
    discrepancies: list[DiscrepancyRow]
    pairs: list[PairRow]
    rules: list[RuleRow]


class RequestResult(BaseModel):
    run: RunSummary
    cached: bool


class DecisionRequest(BaseModel):
    status: str
    note: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str) -> str:
        upper = value.strip().upper()
        if upper not in ("OPEN", "CONFIRMED", "DISMISSED"):
            raise ValueError("status must be one of: OPEN, CONFIRMED, DISMISSED")
        return upper


class ReviewRunRequest(BaseModel):
    verdict: str

    @field_validator("verdict")
    @classmethod
    def _known_verdict(cls, value: str) -> str:
        upper = value.strip().upper()
        if upper not in ("CONFIRM", "DISMISS"):
            raise ValueError("verdict must be one of: CONFIRM, DISMISS")
        return upper


class ReviewRunResult(BaseModel):
    decided: int
    run: RunSummary


class WorkspaceRules(BaseModel):
    rules: list[RuleRow]
    skipped: list[dict[str, Any]]


class DocumentComparisons(BaseModel):
    work_item_id: uuid.UUID
    original_filename: str
    runs: list[RunSummary]


__all__ = ["CorroborationRequest", "DecisionRequest", "DiscrepancyRow", "DocumentComparisons", "PageGeometry",
           "PairRow", "RequestResult", "ReviewRunRequest", "ReviewRunResult", "RuleRow", "RunDetail", "RunDocument",
           "RunDocumentBrief", "RunList", "RunSummary", "WorkspaceRules"]
