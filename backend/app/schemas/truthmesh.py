"""Phase 2 TruthMesh API shapes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MeshStateOut(BaseModel):
    status: str
    last_built_at: Optional[datetime] = None
    build_ms: Optional[int] = None
    engine_version: Optional[str] = None
    error: Optional[str] = None


class MeshNodeOut(BaseModel):
    work_item_id: uuid.UUID
    kind: str
    kind_label: str
    rank: int
    title: str
    filename: str
    document_number: Optional[str] = None
    counterparty: Optional[str] = None
    currency: Optional[str] = None
    amount_micros: Optional[int] = None
    net_amount_micros: Optional[int] = None
    document_date: Optional[str] = None
    effective_date: Optional[str] = None
    end_date: Optional[str] = None
    risk_score: float
    risk_band: str
    degree: int
    open_conflicts: Optional[int] = None


class MeshLinkOut(BaseModel):
    id: uuid.UUID
    source: uuid.UUID
    target: uuid.UUID
    relation: str
    relation_label: str
    directed: bool
    strength: float
    method: str
    signals: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    other: Optional[MeshNodeOut] = None
    outgoing: Optional[bool] = None


class MeshConflictOut(BaseModel):
    id: uuid.UUID
    kind: str
    kind_label: str
    concept: str
    severity: str
    status: str
    title: str
    summary: str
    relation: Optional[str] = None
    work_item_ids: list[uuid.UUID]
    document_values: list[dict[str, Any]] = Field(default_factory=list)
    exposure_micros: Optional[int] = None
    currency: Optional[str] = None
    auto_resolved: bool = False
    resolution_note: Optional[str] = None
    resolved_at: Optional[datetime] = None
    first_seen_at: datetime
    last_seen_at: datetime


class CountOut(BaseModel):
    kind: Optional[str] = None
    relation: Optional[str] = None
    label: str
    count: int


class MeshOverviewOut(BaseModel):
    state: MeshStateOut
    documents: int
    links: int
    clusters: int
    unlinked_documents: int
    open_conflicts: int
    by_severity: dict[str, int]
    by_kind: list[CountOut]
    exposure_micros: int
    exposure_currency: Optional[str] = None
    mixed_currencies: bool
    risk_index: float
    risk_band: str
    relations: list[CountOut]
    kinds: list[CountOut]
    top_risks: list[MeshNodeOut]
    top_conflicts: list[MeshConflictOut]


class MeshGraphOut(BaseModel):
    nodes: list[MeshNodeOut]
    links: list[MeshLinkOut]
    truncated: bool


class MeshDocumentOut(BaseModel):
    node: MeshNodeOut
    facts: list[dict[str, Any]]
    terms: dict[str, Any]
    identifiers: list[str]
    references: list[str]
    text_references: list[str]
    parties: list[str]
    links: list[MeshLinkOut]
    conflicts: list[MeshConflictOut]


class MeshConflictList(BaseModel):
    items: list[MeshConflictOut]
    total: int


class ConflictDecisionIn(BaseModel):
    status: Literal["OPEN", "ACKNOWLEDGED", "RESOLVED", "DISMISSED"]
    note: Optional[str] = Field(default=None, max_length=2000)


class LinkDecisionIn(BaseModel):
    decision: Literal["CONFIRMED", "REJECTED", "AUTO"]


class MatrixRowOut(BaseModel):
    conflict: MeshConflictOut
    cells: dict[str, dict[str, Any]]


class MeshMatrixOut(BaseModel):
    columns: list[MeshNodeOut]
    rows: list[MatrixRowOut]


class RebuildOut(BaseModel):
    job_id: uuid.UUID
    status: str


class SimulationIn(BaseModel):
    work_item_id: uuid.UUID
    scenario: Literal["DELAY", "CLAUSE_INVOKED", "AMOUNT_CHANGE", "TERMINATION", "PARTY_DEFAULT"]
    days: Optional[int] = Field(default=None, ge=1, le=730)
    percent: Optional[float] = Field(default=None, ge=-95, le=500)
    clause: Optional[str] = Field(default=None, max_length=300)
    party: Optional[str] = Field(default=None, max_length=300)

    @field_validator("clause", "party")
    @classmethod
    def _strip(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() or None if isinstance(value, str) else value

    def parameters(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.scenario == "DELAY":
            out["days"] = self.days or 14
        if self.scenario == "AMOUNT_CHANGE":
            out["percent"] = self.percent if self.percent is not None else 10.0
        if self.scenario == "CLAUSE_INVOKED":
            if not self.clause:
                raise ValueError("Name the clause (a number such as 8.2, or its subject).")
            out["clause"] = self.clause
        if self.scenario == "PARTY_DEFAULT" and self.party:
            out["party"] = self.party
        return out


class SimulationSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    origin_work_item_id: uuid.UUID
    scenario: str
    title: str
    parameters: dict[str, Any]
    documents_affected: int
    exposure_micros: Optional[int] = None
    currency: Optional[str] = None
    created_by_user_id: Optional[uuid.UUID] = None
    created_at: datetime


class SimulationOut(SimulationSummaryOut):
    result: dict[str, Any]
