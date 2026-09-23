"""ARCH41-S2:models — extraction memory. The schema's reasoning lives in
alembic/versions/arch41_step1_extraction_memory.py; this module maps it."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SIGNATURE_DIM = 384


def _uuid(**kw: Any) -> Any:
    return mapped_column(PgUUID(as_uuid=True), **kw)


def _ts(nullable: bool = False) -> Any:
    if nullable:
        return mapped_column(DateTime(timezone=True), nullable=True)
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ExtractionMemorySettings(Base):
    __tablename__ = "extraction_memory_settings"

    workspace_id: Mapped[uuid.UUID] = _uuid(primary_key=True)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False, server_default="SHADOW")
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class ExtractionTemplate(Base):
    __tablename__ = "extraction_templates"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[list[float]] = mapped_column(Vector(SIGNATURE_DIM), nullable=False)
    anchor_tokens: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(10), nullable=False, default="LEARNING")
    activated_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class ExtractionTemplateMember(Base):
    __tablename__ = "extraction_template_members"

    work_item_id: Mapped[uuid.UUID] = _uuid(primary_key=True)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    template_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    similarity: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    created_at: Mapped[datetime] = _ts()


class ExtractionExemplar(Base):
    __tablename__ = "extraction_exemplars"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    template_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    work_item_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    verification_field_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    field_path: Mapped[str] = mapped_column(String(200), nullable=False)
    label_source: Mapped[str] = mapped_column(String(10), nullable=False)
    value_form: Mapped[str] = mapped_column(String(5), nullable=False)
    value_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ciphertext: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    key_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    value_token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    line_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    token_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bbox: Mapped[Optional[list[Decimal]]] = mapped_column(ARRAY(Numeric(9, 2)), nullable=True)
    created_at: Mapped[datetime] = _ts()


class ExtractionAnchorRule(Base):
    __tablename__ = "extraction_anchor_rules"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    template_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    field_path: Mapped[str] = mapped_column(String(200), nullable=False)
    anchor_norm: Mapped[str] = mapped_column(String(120), nullable=False)
    offset_dx: Mapped[int] = mapped_column(Integer, nullable=False)
    offset_dy: Mapped[int] = mapped_column(Integer, nullable=False)
    value_token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    value_shape: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    support: Mapped[int] = mapped_column(Integer, nullable=False)
    replay_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replay_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wilson_lower: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    live_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    live_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(8), nullable=False, default="SHADOW")
    activated_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    retired_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    retired_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class ExtractionMemoryTrial(Base):
    __tablename__ = "extraction_memory_trials"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    template_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    state: Mapped[str] = mapped_column(String(9), nullable=False, default="RUNNING")
    on_docs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    off_docs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    on_correction_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 5), nullable=True)
    off_correction_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 5), nullable=True)
    u_statistic: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    p_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6), nullable=True)
    decision_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    started_at: Mapped[datetime] = _ts()
    decided_at: Mapped[Optional[datetime]] = _ts(nullable=True)


class ExtractionMemoryApplication(Base):
    __tablename__ = "extraction_memory_applications"

    work_item_id: Mapped[uuid.UUID] = _uuid(primary_key=True)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    template_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    trial_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    arm: Mapped[str] = mapped_column(String(9), nullable=False)
    injected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exemplar_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(PgUUID(as_uuid=True)), nullable=False, default=list)
    anchor_rule_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(PgUUID(as_uuid=True)), nullable=False, default=list)
    anchor_candidates: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    agreed_fields: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    disagreed_fields: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    memory_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fields_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fields_corrected: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    corrected_fields: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    outcome_recorded_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


__all__ = [
    "ExtractionAnchorRule",
    "ExtractionExemplar",
    "ExtractionMemoryApplication",
    "ExtractionMemorySettings",
    "ExtractionMemoryTrial",
    "ExtractionTemplate",
    "ExtractionTemplateMember",
    "SIGNATURE_DIM",
]
