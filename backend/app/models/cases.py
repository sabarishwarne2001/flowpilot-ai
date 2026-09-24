"""ARCH43-S1:models-cases — case templates, cases and missing-document requests.

Schema (every CHECK, FK, the immutability trigger and the single-use token
constraint) lives in alembic/versions/arch43_step1_case_intelligence.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import CHAR, DateTime, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid(nullable: bool = False) -> Any:
    return mapped_column(PgUUID(as_uuid=True), nullable=nullable)


def _ts(nullable: bool = True) -> Any:
    return mapped_column(DateTime(timezone=True), nullable=nullable, server_default=None if nullable else text("now()"))


class CaseTemplate(Base):
    __tablename__ = "case_templates"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID] = _uuid()
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="DRAFT")
    assembly_key: Mapped[str] = mapped_column(String(8), nullable=False)
    entity_kind: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    entity_role: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    required_documents: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    rules: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    request_ttl_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=72)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(True)
    created_at: Mapped[datetime] = _ts(False)
    published_at: Mapped[Optional[datetime]] = _ts()
    retired_at: Mapped[Optional[datetime]] = _ts()


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID] = _uuid()
    template_id: Mapped[uuid.UUID] = _uuid()
    anchor_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    anchor_entity_id: Mapped[Optional[uuid.UUID]] = _uuid(True)
    anchor_batch_id: Mapped[Optional[uuid.UUID]] = _uuid(True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="INCOMPLETE")
    completeness: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("0"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(True)
    created_at: Mapped[datetime] = _ts(False)
    evaluated_at: Mapped[Optional[datetime]] = _ts()
    completed_at: Mapped[Optional[datetime]] = _ts()
    closed_at: Mapped[Optional[datetime]] = _ts()


class CaseDocument(Base):
    __tablename__ = "case_documents"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID] = _uuid()
    work_item_id: Mapped[uuid.UUID] = _uuid()
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    added_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(True)
    added_at: Mapped[datetime] = _ts(False)


class CaseRuleResult(Base):
    __tablename__ = "case_rule_results"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID] = _uuid()
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    op: Mapped[str] = mapped_column(String(12), nullable=False)
    outcome: Mapped[str] = mapped_column(String(8), nullable=False)
    left_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    right_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evaluated_at: Mapped[datetime] = _ts(False)


class DocumentRequest(Base):
    __tablename__ = "document_requests"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID] = _uuid()
    case_id: Mapped[uuid.UUID] = _uuid()
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient_label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)  # ARCH43-S1:model-char-token
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="OPEN")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = _ts()
    fulfilled_work_item_id: Mapped[Optional[uuid.UUID]] = _uuid(True)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(True)
    created_at: Mapped[datetime] = _ts(False)


__all__ = ["Case", "CaseDocument", "CaseRuleResult", "CaseTemplate", "DocumentRequest"]
