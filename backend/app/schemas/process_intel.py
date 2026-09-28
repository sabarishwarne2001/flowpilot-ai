"""ARCH49-S1:schemas — Process Intelligence & the Governed Exception Agent over REST.

Mirrored by frontend/src/types/process.ts; verify_arch49 W3 compares the field
lists of the models below with the console's interfaces.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

ObjectType = Literal["DOCUMENT", "CASE", "POSTING", "REVIEW_ITEM", "FINDING", "EXECUTION"]
SlaObjectType = Literal["REVIEW_ITEM", "CASE", "POSTING"]
ProposalStatus = Literal["PROPOSED", "AUTO_SCHEDULED", "APPLIED", "AUTO_APPLIED", "REJECTED", "UNDONE", "SUPERSEDED",
                         "FAILED"]
RejectReason = Literal["WRONG_DECISION", "WRONG_EVIDENCE", "NEEDS_CONTEXT", "NOT_NOW", "OTHER"]


class ObjectTypeSummary(BaseModel):
    object_type: str
    label: str
    objects: int
    events: int
    shares_events_with: dict[str, int]


class SourceCursor(BaseModel):
    source: str
    watermark: datetime
    caught_up: bool
    last_run_at: datetime
    events_written: int


class ModelRun(BaseModel):
    id: str
    status: str
    reason: Optional[str] = None
    target_hours: int
    instances_train: int
    instances_holdout: int
    snapshots_train: int
    snapshots_holdout: int
    base_rate: Optional[float] = None
    brier: Optional[float] = None
    brier_baseline: Optional[float] = None
    skill: Optional[float] = None
    reliability: list[dict[str, Any]] = Field(default_factory=list)
    importances: dict[str, float] = Field(default_factory=dict)
    trained_at: datetime


class AgentPolicyOut(BaseModel):
    planning_enabled: bool
    auto_apply_enabled: bool
    auto_apply_kinds: list[str]
    hold_minutes: int
    enabled_by_user_id: Optional[uuid.UUID] = None
    enabled_at: Optional[datetime] = None
    is_default: bool
    auto_capable_kinds: list[str]


class AgentPolicyIn(BaseModel):
    planning_enabled: bool = True
    auto_apply_enabled: bool = False
    auto_apply_kinds: list[str] = Field(default_factory=list, max_length=8)
    hold_minutes: int = Field(default=30, ge=5, le=1440)


class ProcessOverview(BaseModel):
    events: int
    first_at: Optional[datetime] = None
    last_at: Optional[datetime] = None
    object_types: list[ObjectTypeSummary]
    sources: list[SourceCursor]
    runs: dict[str, Optional[ModelRun]]
    at_risk: int
    breached: int
    proposals: dict[str, int]
    policy: AgentPolicyOut


class DiscoveryOut(BaseModel):
    object_type: str
    window_days: int
    truncated: bool
    throughput_seconds: dict[str, float]
    graph: dict[str, Any]
    variants: list[dict[str, Any]]
    variant_count: int
    cost: Optional[dict[str, Any]] = None
    touches_per_object: float


class TimelineEvent(BaseModel):
    id: str
    activity: str
    occurred_at: datetime
    source: str
    actor_kind: str
    actor_user_id: Optional[str] = None
    attributes: dict[str, Any]
    objects: list[dict[str, str]]


class TimelineOut(BaseModel):
    object_type: str
    object_id: uuid.UUID
    events: list[TimelineEvent]


class ConformanceOut(BaseModel):
    window_days: int
    flows: list[dict[str, Any]]
    templates: list[dict[str, Any]]


class SlaPolicy(BaseModel):
    object_type: str
    target_hours: int
    at_risk_probability: float
    alerts_enabled: bool
    is_default: bool
    updated_at: Optional[datetime] = None


class SlaPolicyIn(BaseModel):
    target_hours: int = Field(ge=1, le=24 * 90)
    at_risk_probability: float = Field(gt=0, lt=1)
    alerts_enabled: bool = True


class Prediction(BaseModel):
    object_type: str
    object_id: uuid.UUID
    kind: Optional[str] = None
    started_at: datetime
    due_at: datetime
    probability: float
    state: str
    predicted_at: datetime
    alerted_at: Optional[datetime] = None


class SlaOut(BaseModel):
    policies: list[SlaPolicy]
    runs: dict[str, Optional[ModelRun]]
    predictions: list[Prediction]


class CostOut(BaseModel):
    window_days: int
    by_object_type: dict[str, dict[str, Any]]


class SweepOut(BaseModel):
    report: dict[str, Any]


class ProposalRow(BaseModel):
    id: uuid.UUID
    subject_type: str
    subject_kind: str
    subject_id: uuid.UUID
    work_item_id: Optional[uuid.UUID] = None
    proposal_kind: str
    label: str
    status: ProposalStatus
    confidence: float
    calibrated_probability: Optional[float] = None
    subject_version: int
    verdict: Optional[str] = None
    rationale: list[str]
    holds: list[str]
    auto: bool
    apply_after: Optional[datetime] = None
    waiting_until: Optional[datetime] = None
    injection_suspected: bool
    decided_by_user_id: Optional[uuid.UUID] = None
    decided_at: Optional[datetime] = None
    reject_reason: Optional[str] = None
    applied_at: Optional[datetime] = None
    resolution: Optional[str] = None
    failure: Optional[str] = None
    created_at: datetime


class ProposalList(BaseModel):
    items: list[ProposalRow]
    total: int
    counts: dict[str, int]


class ToolCallOut(BaseModel):
    seq: int
    tool: str
    arguments: dict[str, Any]
    outcome: str
    detail: dict[str, Any]


class ExcerptOut(BaseModel):
    """What a source says, as delimited, untrusted data (rendered as a quote; never as instructions)."""

    label: str
    fenced_text: str
    fence_nonce: str
    injection_flags: dict[str, int]


class ProposalDetail(BaseModel):
    proposal: ProposalRow
    evidence: dict[str, Any]
    autonomy: dict[str, Any]
    tool_calls: list[ToolCallOut]
    excerpts: list[ExcerptOut]


class RejectIn(BaseModel):
    reason: RejectReason


class ApproveOut(BaseModel):
    proposal: ProposalRow
    resolution: Optional[str] = None
    upload_path: Optional[str] = None


__all__ = [name for name in dir() if name[0].isupper()]
