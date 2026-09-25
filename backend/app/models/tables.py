"""ARCH44-S1:models — extracted tables, their cells, validations and learned column mappings.

The schema (every CHECK, the composite FKs and the grid trigger) lives in
alembic/versions/arch44_step1_table_intelligence.py; these mappings are the
ORM view, with column types matching it exactly (verify_arch44 D2 compares).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import CHAR, Date, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid(*args: Any, **kw: Any) -> Any:
    return mapped_column(PgUUID(as_uuid=True), *args, **kw)


def _ts(nullable: bool = False) -> Any:
    if nullable:
        return mapped_column(DateTime(timezone=True), nullable=True)
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


class ExtractedTable(Base):
    __tablename__ = "extracted_tables"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    work_item_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    n_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    n_cols: Mapped[int] = mapped_column(Integer, nullable=False)
    header_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    rotation: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=sa_text("0"))
    skew_degrees: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, server_default=sa_text("0"))
    columns: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    rows: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    bboxes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    checked_relations: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    failed_checks: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    layout_key: Mapped[str] = mapped_column(CHAR(40), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(24), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("1"))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    corrected_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    corrected_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ExtractedTableCell(Base):
    __tablename__ = "extracted_table_cells"

    table_id: Mapped[uuid.UUID] = _uuid(primary_key=True)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    row_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    col_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    row_span: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("1"))
    col_span: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("1"))
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa_text("''"))
    value_type: Mapped[str] = mapped_column(String(8), nullable=False)
    value_number: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 6), nullable=True)
    value_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    ocr_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, server_default=sa_text("1"))
    base_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    flags: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False, default=list)
    bbox: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    original_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    corrected_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    corrected_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class TableValidation(Base):
    __tablename__ = "table_validations"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    table_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(8), nullable=False)
    outcome: Mapped[str] = mapped_column(String(4), nullable=False)
    row_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    col_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    expected: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 6), nullable=True)
    actual: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 6), nullable=True)
    checked: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    message: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa_text("''"))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _ts()


class TableColumnMapping(Base):
    __tablename__ = "table_column_mappings"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    layout_key: Mapped[str] = mapped_column(CHAR(40), nullable=False)
    header_key: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmations: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    contradictions: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    template_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


__all__ = ["ExtractedTable", "ExtractedTableCell", "TableColumnMapping", "TableValidation"]
