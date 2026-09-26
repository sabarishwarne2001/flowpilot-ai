"""ARCH47-S1:models — ERP targets, mapping versions, lookup tables, the posting ledger and its attempts.

The schema (every CHECK, the composite FKs, the ledger's UNIQUE key) lives in
alembic/versions/arch47_step1_erp_posting.py; these mappings are the ORM view,
with column types matching it exactly (verify_arch47 D2 compares).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (CHAR, Boolean, BigInteger, DateTime, ForeignKey, Integer, LargeBinary, Numeric, SmallInteger,
                        String, Text)
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


def _user_fk() -> Any:
    return _uuid(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ErpTarget(Base):
    __tablename__ = "erp_targets"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    transport: Mapped[str] = mapped_column(String(10), nullable=False)
    preset: Mapped[str] = mapped_column(String(20), nullable=False, server_default=sa_text("'NONE'"))
    ack_mode: Mapped[str] = mapped_column(String(10), nullable=False)
    auth_mode: Mapped[str] = mapped_column(String(28), nullable=False, server_default=sa_text("'NONE'"))
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default=sa_text("'ACTIVE'"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    credential_ciphertext: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    credential_fingerprint: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    credential_updated_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    auto_post: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("false"))
    auto_post_since: Mapped[Optional[datetime]] = _ts(nullable=True)
    auto_sources: Mapped[list[str]] = mapped_column(ARRAY(String(20)), nullable=False, default=list)
    auto_objects: Mapped[list[str]] = mapped_column(ARRAY(String(20)), nullable=False, default=list)
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=sa_text("6"))
    ack_timeout_hours: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("72"))
    control_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sa_text("0"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("1"))
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class ErpMapping(Base):
    __tablename__ = "erp_mappings"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    target_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    object_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default=sa_text("'ACTIVE'"))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    spec_sha: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    created_at: Mapped[datetime] = _ts()
    retired_at: Mapped[Optional[datetime]] = _ts(nullable=True)


class ErpLookupTable(Base):
    __tablename__ = "erp_lookup_tables"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    entries: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("1"))
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class ErpPosting(Base):
    __tablename__ = "erp_postings"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    target_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    mapping_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    mapping_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    object_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    work_item_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    origin: Mapped[str] = mapped_column(String(8), nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False, server_default=sa_text("'PENDING'"))
    idempotency_key: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    source_digest: Mapped[Optional[str]] = mapped_column(CHAR(64), nullable=True)
    engine_version: Mapped[str] = mapped_column(String(16), nullable=False)
    document_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(CHAR(3), nullable=True)
    # ARCH47-S1:sql-null. None is SQL NULL here (not the JSON literal null): erasure and the CHECKs rely on it.
    canonical: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB(none_as_null=True), nullable=True)
    mapped: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB(none_as_null=True), nullable=True)
    rendered: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    rendered_media_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    rendered_filename: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    content_sha: Mapped[Optional[str]] = mapped_column(CHAR(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("6"))
    next_attempt_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    lease_until: Mapped[Optional[datetime]] = _ts(nullable=True)
    lease_token: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    ack: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    control_numbers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    remote_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    exception_seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    delivered_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    acknowledged_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    review_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    erased_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class ErpPostingAttempt(Base):
    __tablename__ = "erp_posting_attempts"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    posting_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actor_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    started_at: Mapped[datetime] = _ts()
    finished_at: Mapped[Optional[datetime]] = _ts(nullable=True)


__all__ = ["ErpLookupTable", "ErpMapping", "ErpPosting", "ErpPostingAttempt", "ErpTarget"]
