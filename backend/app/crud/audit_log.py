"""
Audit log database reads (ARCH-07 Step 4).

Read-only database access layer. Writes happen through app.services.audit_service.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.schemas.audit_log import AuditLogFilters


def _base_query(organization_id: uuid.UUID, filters: AuditLogFilters) -> Select:
    stmt = select(AuditLog).where(AuditLog.organization_id == organization_id)

    if filters.resource_type is not None:
        stmt = stmt.where(AuditLog.resource_type == filters.resource_type)
    if filters.action is not None:
        stmt = stmt.where(AuditLog.action == filters.action)
    if filters.actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == filters.actor_id)
    if filters.resource_id is not None:
        stmt = stmt.where(AuditLog.resource_id == filters.resource_id)
    if filters.workspace_id is not None:
        stmt = stmt.where(AuditLog.workspace_id == filters.workspace_id)
    if filters.date_from is not None:
        stmt = stmt.where(AuditLog.created_at >= filters.date_from)
    if filters.date_to is not None:
        stmt = stmt.where(AuditLog.created_at <= filters.date_to)

    return stmt


def list_for_organization(
    db: Session,
    *,
    organization_id: uuid.UUID,
    filters: AuditLogFilters,
    limit: int,
    offset: int,
) -> tuple[list[AuditLog], int]:
    stmt = _base_query(organization_id, filters)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar_one()

    page_stmt = (
        stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = list(db.execute(page_stmt).scalars().all())
    return rows, total


def get_for_organization(
    db: Session, *, organization_id: uuid.UUID, audit_log_id: uuid.UUID
) -> Optional[AuditLog]:
    stmt = select(AuditLog).where(
        AuditLog.organization_id == organization_id,
        AuditLog.id == audit_log_id,
    )
    return db.execute(stmt).scalar_one_or_none()