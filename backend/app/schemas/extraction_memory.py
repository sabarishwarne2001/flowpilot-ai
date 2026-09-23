"""ARCH41-S2:schemas — extraction memory API shapes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class MemorySettingsResponse(BaseModel):
    mode: Literal["OFF", "SHADOW", "AUTO"]
    is_default: bool
    updated_at: Optional[datetime] = None


class MemorySettingsUpdate(BaseModel):
    mode: Literal["OFF", "SHADOW", "AUTO"]


class MemoryPotentialResponse(BaseModel):
    reviewed_documents: int
    corrected_fields: int


class MemorySummaryResponse(BaseModel):
    mode: Literal["OFF", "SHADOW", "AUTO"]
    templates: int
    active_templates: int
    exemplar_documents: int
    corrected_exemplars: int
    active_rules: int
    shadow_rules: int
    running_trials: int
    baseline_correction_rate: Optional[float] = None
    memory_correction_rate: Optional[float] = None


class FieldMetric(BaseModel):
    field_path: str
    corrections: int
    confirmations: int
    baseline_rate: Optional[float] = None
    memory_rate: Optional[float] = None


class TemplateRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_type: str
    state: str
    member_count: int
    exemplar_documents: int
    anchor_tokens: list[str]
    activated_at: Optional[datetime] = None
    baseline_correction_rate: Optional[float] = None
    memory_correction_rate: Optional[float] = None
    fields: list[FieldMetric] = Field(default_factory=list)


class TemplateAction(BaseModel):
    action: Literal["reset"]


class RuleRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    template_id: uuid.UUID
    field_path: str
    anchor_norm: str
    offset_dx: int
    offset_dy: int
    value_token_count: int
    support: int
    replay_hits: int
    replay_total: int
    wilson_lower: Optional[float] = None
    live_hits: int
    live_total: int
    state: str
    activated_at: Optional[datetime] = None
    retired_reason: Optional[str] = None
    sentence: str


class RuleAction(BaseModel):
    action: Literal["retire", "shadow"]


class TrialRow(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    document_type: str
    state: str
    on_docs: int
    off_docs: int
    on_correction_rate: Optional[float] = None
    off_correction_rate: Optional[float] = None
    p_value: Optional[float] = None
    decision_reason: Optional[str] = None
    started_at: datetime
    decided_at: Optional[datetime] = None
    sentence: str


class LearnedField(BaseModel):
    field_path: str
    corrections: int
    confirmations: int
    sentence: str
    anchor_state: Optional[str] = None


class WorkItemMemoryResponse(BaseModel):
    applied: bool
    arm: Optional[str] = None
    injected: bool = False
    template_id: Optional[uuid.UUID] = None
    document_type: Optional[str] = None
    template_state: Optional[str] = None
    layout_documents: int = 0
    exemplar_documents_used: int = 0
    fields: list[LearnedField] = Field(default_factory=list)
    headline: str
