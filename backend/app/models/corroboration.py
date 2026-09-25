"""ARCH45-S1:models — corroboration runs, their documents, pair summaries and discrepancies.

The schema (every CHECK, the composite FKs, the live-fingerprint index and the
two triggers) lives in alembic/versions/arch45_step1_corroboration.py; these
mappings are the ORM view, with column types matching it exactly
(verify_arch45 D2 compares with alembic's autogenerate).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import CHAR, Boolean, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid(*args: Any, **kw: Any) -> Any:
    return mapped_column(PgUUID(as_uuid=True), *args, **kw)


def _ts(nullable: bool = False) -> Any:
    if nullable:
        return mapped_column(DateTime(timezone=True), nullable=True)
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


def _count() -> Any:
    return mapped_column(Integer, nullable=False, server_default=sa_text("0"))


class CorroborationRun(Base):
    __tablename__ = "corroboration_runs"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    set_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(24), nullable=False)
    encoder: Mapped[str] = mapped_column(String(32), nullable=False)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    rules: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    layers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    anchors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    document_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    discrepancy_count: Mapped[int] = _count()
    material_count: Mapped[int] = _count()
    open_material_count: Mapped[int] = _count()
    max_materiality: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, server_default=sa_text("0"))
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("1"))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    started_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    completed_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    stale_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class CorroborationDocument(Base):
    __tablename__ = "corroboration_documents"

    run_id: Mapped[uuid.UUID] = _uuid(primary_key=True)
    work_item_id: Mapped[uuid.UUID] = _uuid(primary_key=True)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = _ts()


class CorroborationPair(Base):
    __tablename__ = "corroboration_pairs"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    left_work_item_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    right_work_item_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    clauses_left: Mapped[int] = _count()
    clauses_right: Mapped[int] = _count()
    clauses_matched: Mapped[int] = _count()
    clauses_identical: Mapped[int] = _count()
    clause_similarity: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, server_default=sa_text("0"))
    fields_compared: Mapped[int] = _count()
    fields_agreeing: Mapped[int] = _count()
    lines_left: Mapped[int] = _count()
    lines_right: Mapped[int] = _count()
    lines_matched: Mapped[int] = _count()
    lines_agreeing: Mapped[int] = _count()
    entities_compared: Mapped[int] = _count()
    entities_agreeing: Mapped[int] = _count()
    discrepancy_count: Mapped[int] = _count()
    material_count: Mapped[int] = _count()
    agreement: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, server_default=sa_text("1"))
    alignment: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = _ts()


class Discrepancy(Base):
    __tablename__ = "discrepancies"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    layer: Mapped[str] = mapped_column(String(8), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    group_key: Mapped[str] = mapped_column(String(300), nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa_text("''"))
    materiality: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    is_material: Mapped[bool] = mapped_column(Boolean, nullable=False)
    doc_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default=sa_text("'OPEN'"))
    decided_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    decided_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts()


__all__ = ["CorroborationDocument", "CorroborationPair", "CorroborationRun", "Discrepancy"]
