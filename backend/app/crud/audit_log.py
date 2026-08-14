"""
Audit log database reads (ARCH-07 Step 4, ARCH-08 Step 2, Step 3, Step 8).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import ColumnElement, DateTime, Select, select, tuple_
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Session

from app.core.pagination import KeysetCursor
from app.models.audit_log import AuditLog
from app.schemas.audit_log import AuditLogFilters


def _predicates(
    organization_id: uuid.UUID, filters: AuditLogFilters
) -> list[ColumnElement[bool]]:
    preds: list[ColumnElement[bool]] = [AuditLog.organization_id == organization_id]

    if filters.resource_type is not None:
        preds.append(AuditLog.resource_type == filters.resource_type)
    if filters.action is not None:
        preds.append(AuditLog.action == filters.action)
    if filters.outcome is not None:
        preds.append(AuditLog.outcome == filters.outcome)
    if filters.actor_id is not None:
        preds.append(AuditLog.actor_id == filters.actor_id)
    if filters.api_key_id is not None:
        preds.append(AuditLog.api_key_id == filters.api_key_id)
    if filters.resource_id is not None:
        preds.append(AuditLog.resource_id == filters.resource_id)
    if filters.workspace_id is not None:
        preds.append(AuditLog.workspace_id == filters.workspace_id)
    if filters.date_from is not None:
        preds.append(AuditLog.created_at >= filters.date_from)
    if filters.date_to is not None:
        preds.append(AuditLog.created_at <= filters.date_to)

    return preds


def _base_query(organization_id: uuid.UUID, filters: AuditLogFilters) -> Select:
    return select(AuditLog).where(*_predicates(organization_id, filters))


def _apply_cursor(stmt: Select, cursor: Optional[KeysetCursor]) -> Select:
    if cursor is None:
        return stmt
    return stmt.where(
        tuple_(AuditLog.created_at, AuditLog.id)
        < tuple_(
            sa.literal(cursor.created_at, type_=DateTime(timezone=True)),
            sa.literal(cursor.id, type_=PgUUID(as_uuid=True)),
        )
    )


def list_for_organization(
    db: Session,
    *,
    organization_id: uuid.UUID,
    filters: AuditLogFilters,
    limit: int,
    cursor: Optional[KeysetCursor] = None,
) -> tuple[list[AuditLog], bool]:
    stmt = (
        _apply_cursor(_base_query(organization_id, filters), cursor)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit + 1)
    )
    rows = list(db.execute(stmt).scalars().all())
    has_more = len(rows) > limit
    return rows[:limit], has_more


def get_for_organization(
    db: Session, *, organization_id: uuid.UUID, audit_log_id: uuid.UUID
) -> Optional[AuditLog]:
    stmt = select(AuditLog).where(
        AuditLog.organization_id == organization_id,
        AuditLog.id == audit_log_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def newest_cursor(
    db: Session, *, organization_id: uuid.UUID, filters: AuditLogFilters
) -> Optional[tuple[datetime, uuid.UUID]]:
    stmt = (
        select(AuditLog.created_at, AuditLog.id)
        .where(*_predicates(organization_id, filters))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(1)
    )
    res = db.execute(stmt).first()
    return (res[0], res[1]) if res else None


def cursor_at_offset(
    db: Session,
    *,
    organization_id: uuid.UUID,
    filters: AuditLogFilters,
    anchor: tuple[datetime, uuid.UUID],
    offset: int,
) -> Optional[tuple[datetime, uuid.UUID]]:
    stmt = (
        select(AuditLog.created_at, AuditLog.id)
        .where(
            *_predicates(organization_id, filters),
            tuple_(AuditLog.created_at, AuditLog.id) <= tuple_(
                sa.literal(anchor[0], type_=DateTime(timezone=True)),
                sa.literal(anchor[1], type_=PgUUID(as_uuid=True)),
            )
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(1)
        .offset(offset)
    )
    res = db.execute(stmt).first()
    return (res[0], res[1]) if res else None


def fetch_export_batch(
    db: Session,
    *,
    organization_id: uuid.UUID,
    filters: AuditLogFilters,
    anchor: tuple[datetime, uuid.UUID],
    cursor: Optional[KeysetCursor],
    limit: int,
) -> list[tuple[Any, ...]]:
    stmt = select(
        AuditLog.id,
        AuditLog.created_at,
        AuditLog.organization_id,
        AuditLog.workspace_id,
        AuditLog.actor_id,
        AuditLog.api_key_id,
        AuditLog.resource_type,
        AuditLog.resource_id,
        AuditLog.action,
        AuditLog.outcome,
        AuditLog.ip_address,
        AuditLog.user_agent,
        AuditLog.details,
    ).where(
        *_predicates(organization_id, filters),
        tuple_(AuditLog.created_at, AuditLog.id) <= tuple_(
            sa.literal(anchor[0], type_=DateTime(timezone=True)),
            sa.literal(anchor[1], type_=PgUUID(as_uuid=True)),
        )
    )

    if cursor is not None:
        stmt = stmt.where(
            tuple_(AuditLog.created_at, AuditLog.id) < tuple_(
                sa.literal(cursor.created_at, type_=DateTime(timezone=True)),
                sa.literal(cursor.id, type_=PgUUID(as_uuid=True)),
            )
        )

    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
    return list(db.execute(stmt).all())