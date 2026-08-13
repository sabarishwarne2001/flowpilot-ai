"""
Organization audit log read API (ARCH-07 Step 4).

    GET /organizations/{organization_id}/audit-logs
    GET /organizations/{organization_id}/audit-logs/{audit_log_id}
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import RequireOrgAdmin, get_db
from app.crud import audit_log as audit_log_crud
from app.models.audit_log import AuditAction, AuditResourceType
from app.schemas.audit_log import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    AuditLogFilters,
    AuditLogPage,
    AuditLogRead,
)

router = APIRouter(tags=["Audit Logs"])


def _filters(
    resource_type: Optional[AuditResourceType] = Query(None),
    action: Optional[AuditAction] = Query(None),
    actor_id: Optional[uuid.UUID] = Query(None),
    resource_id: Optional[uuid.UUID] = Query(None),
    workspace_id: Optional[uuid.UUID] = Query(None),
    date_from: Optional[datetime] = Query(
        None, description="Inclusive lower bound on created_at (ISO 8601)."
    ),
    date_to: Optional[datetime] = Query(
        None, description="Inclusive upper bound on created_at (ISO 8601)."
    ),
) -> AuditLogFilters:
    try:
        return AuditLogFilters(
            resource_type=resource_type,
            action=action,
            actor_id=actor_id,
            resource_id=resource_id,
            workspace_id=workspace_id,
            date_from=date_from,
            date_to=date_to,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get(
    "/organizations/{organization_id}/audit-logs",
    response_model=AuditLogPage,
    summary="List audit events for an organization",
)
def list_audit_logs(
    organization_id: uuid.UUID,
    db: Session = Depends(get_db),
    context=Depends(RequireOrgAdmin),
    filters: AuditLogFilters = Depends(_filters),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
) -> AuditLogPage:
    rows, total = audit_log_crud.list_for_organization(
        db,
        organization_id=context.organization.id,
        filters=filters,
        limit=limit,
        offset=offset,
    )
    return AuditLogPage(
        items=[AuditLogRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/organizations/{organization_id}/audit-logs/{audit_log_id}",
    response_model=AuditLogRead,
    summary="Fetch a single audit event",
)
def get_audit_log(
    organization_id: uuid.UUID,
    audit_log_id: uuid.UUID,
    db: Session = Depends(get_db),
    context=Depends(RequireOrgAdmin),
) -> AuditLogRead:
    entry = audit_log_crud.get_for_organization(
        db, organization_id=context.organization.id, audit_log_id=audit_log_id
    )
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Audit log not found."
        )
    return AuditLogRead.model_validate(entry)