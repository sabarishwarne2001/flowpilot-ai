"""ARCH44-S1:schemas — table intelligence API models. The console mirrors these in
frontend/src/types/tables.ts; verify_arch44 T9 compares the field lists."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class TableColumn(BaseModel):
    index: int
    path: list[str]
    key: str
    header_key: str
    value_type: str
    date_order: str
    role: str
    role_source: str


class TableRowInfo(BaseModel):
    index: int
    kind: str
    level: int
    page: int
    parent: Optional[int] = None


class TableCell(BaseModel):
    row: int
    col: int
    row_span: int
    col_span: int
    page: int
    text: str
    value_type: str
    value_number: Optional[str] = None
    value_date: Optional[date] = None
    confidence: float
    base_confidence: float
    flags: list[str]
    original_text: Optional[str] = None
    bbox: Optional[dict[str, float]] = None


class TableValidationRow(BaseModel):
    kind: str
    scope: str
    outcome: str
    row_index: Optional[int] = None
    col_index: Optional[int] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    checked: int
    failed: int
    message: str
    detail: dict[str, Any]


class TableSummary(BaseModel):
    id: uuid.UUID
    work_item_id: uuid.UUID
    original_filename: str
    ordinal: int
    title: Optional[str] = None
    page_start: int
    page_end: int
    n_rows: int
    n_cols: int
    header_rows: int
    method: str
    rotation: int
    skew_degrees: float
    confidence: float
    status: str
    failed_checks: int
    checked_relations: int
    layout_key: str
    revision: int
    created_at: datetime
    corrected_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None


class TableList(BaseModel):
    items: list[TableSummary]
    total: int
    counts_by_status: dict[str, int]


class LearnedMapping(BaseModel):
    header_key: str
    role: str
    confirmations: int
    contradictions: int
    applied: bool


class TableDetail(BaseModel):
    table: TableSummary
    columns: list[TableColumn]
    rows: list[TableRowInfo]
    cells: list[TableCell]
    validations: list[TableValidationRow]
    learned_mappings: list[LearnedMapping]


class DocumentTables(BaseModel):
    work_item_id: uuid.UUID
    original_filename: str
    page_count: Optional[int] = None
    extractable: bool
    tables: list[TableSummary]


class ExtractResult(BaseModel):
    ran: str
    reason: Optional[str] = None
    tables: list[TableSummary]


class CellEdit(BaseModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    text: str = Field(default="", max_length=2000)


class CellCorrection(BaseModel):
    cells: list[CellEdit] = Field(min_length=1, max_length=500)


class ColumnRoleRequest(BaseModel):
    role: str = Field(min_length=1, max_length=16)


class TableReviewRequest(BaseModel):
    verdict: str

    @field_validator("verdict")
    @classmethod
    def _known(cls, value: str) -> str:
        upper = (value or "").strip().upper()
        if upper not in ("ACCEPT", "REJECT"):
            raise ValueError("verdict must be one of: ACCEPT, REJECT")
        return upper
