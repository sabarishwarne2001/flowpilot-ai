"""
Organization audit log read and export API (ARCH-07 Step 4, ARCH-08 Step 2, Step 3).

    GET /organizations/{organization_id}/audit-logs/export
    GET /organizations/{organization_id}/audit-logs
    GET /organizations/{organization_id}/audit-logs/{audit_log_id}
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import RequireOrgAdmin, get_db, get_read_db
from app.core.pagination import (
    CursorFilterMismatchError,
    InvalidCursorError,
    KeysetCursor,
    decode_cursor,
    encode_cursor,
    filter_digest,
)
from app.crud import audit_log as audit_log_crud
from app.models.audit_log import AuditAction, AuditResourceType
from app.schemas.audit_log import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    AuditLogFilters,
    AuditLogPage,
    AuditLogRead,
)
from app.services import audit_service
from app.services.audit_export_service import (
    AUDIT_EXPORT_MAX_ROWS,
    EXPORT_COLUMNS,
    AuditExportFormat,
    stream_export,
)

router = APIRouter(tags=["Audit Logs"])

_MEDIA_TYPES = {
    AuditExportFormat.CSV: "text/csv; charset=utf-8",
    AuditExportFormat.JSONL: "application/x-ndjson",
}


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


# ============================================================================
# Export Route (MUST BE DECLARED ABOVE /{audit_log_id})
# ============================================================================

@router.get(
    "/organizations/{organization_id}/audit-logs/export",
    summary="Export audit events as CSV or JSONL",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}, "application/x-ndjson": {}}}},
)
def export_audit_logs(
    organization_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context=Depends(RequireOrgAdmin),
    filters: AuditLogFilters = Depends(_filters),
    fmt: AuditExportFormat = Query(AuditExportFormat.CSV, alias="format"),
) -> StreamingResponse:
    org_id = context.organization.id

    anchor = audit_log_crud.newest_cursor(db, organization_id=org_id, filters=filters)

    truncation = (
        audit_log_crud.cursor_at_offset(
            db,
            organization_id=org_id,
            filters=filters,
            anchor=anchor,
            offset=AUDIT_EXPORT_MAX_ROWS - 1,
        )
        if anchor is not None
        else None
    )
    truncated = truncation is not None
    digest = filter_digest(filters.digest_scope(organization_id=org_id))

    audit_service.record(
        db,
        organization_id=org_id,
        actor_id=context.user.id,
        resource_type=AuditResourceType.AUDIT_LOG,
        resource_id=None,
        action=AuditAction.EXPORTED,
        details={
            **audit_service.actor_snapshot(context.user),
            "format": fmt.value,
            "row_cap": AUDIT_EXPORT_MAX_ROWS,
            "truncated": truncated,
        },
        **audit_service.context_from_request(request),
    )
    db.commit()

    org_slug = getattr(context.organization, "slug", "export")
    filename = f"audit-{org_slug}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.{fmt.value}"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "X-FlowPilot-Export-Truncated": "true" if truncated else "false",
    }

    if truncated and truncation is not None:
        headers["Link"] = (
            f'<{request.url.path}?format={fmt.value}'
            f'&cursor={encode_cursor(created_at=truncation[0], id=truncation[1], digest=digest)}>; '
            'rel="next"'
        )

    if anchor is None:
        header_line = ",".join(EXPORT_COLUMNS) + "\n" if fmt is AuditExportFormat.CSV else ""
        return StreamingResponse(
            iter([header_line] if header_line else []),
            media_type=_MEDIA_TYPES[fmt],
            headers=headers,
        )

    return StreamingResponse(
        stream_export(organization_id=org_id, filters=filters, anchor=anchor, fmt=fmt, db=db),
        media_type=_MEDIA_TYPES[fmt],
        headers=headers,
    )


# ============================================================================
# Paged List Route
# ============================================================================

@router.get(
    "/organizations/{organization_id}/audit-logs",
    response_model=AuditLogPage,
    summary="List audit events for an organization",
)
def list_audit_logs(
    organization_id: uuid.UUID,
    db: Session = Depends(get_read_db),
    context=Depends(RequireOrgAdmin),
    filters: AuditLogFilters = Depends(_filters),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: Optional[str] = Query(
        None, description="Opaque cursor from a previous page's next_cursor."
    ),
    offset: Optional[int] = Query(
        None,
        deprecated=True,
        description="REMOVED in ARCH-08 Step 2. Use cursor.",
    ),
) -> AuditLogPage:
    if offset is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "offset pagination was removed in ARCH-08. Use the cursor "
                "returned in next_cursor."
            ),
        )

    digest = filter_digest(filters.digest_scope(organization_id=context.organization.id))

    decoded: Optional[KeysetCursor] = None
    if cursor is not None:
        try:
            decoded = decode_cursor(cursor, expected_digest=digest)
        except CursorFilterMismatchError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        except InvalidCursorError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Malformed cursor.") from exc

    rows, has_more = audit_log_crud.list_for_organization(
        db,
        organization_id=context.organization.id,
        filters=filters,
        limit=limit,
        cursor=decoded,
    )

    next_cursor = (
        encode_cursor(created_at=rows[-1].created_at, id=rows[-1].id, digest=digest)
        if has_more and rows
        else None
    )

    return AuditLogPage(
        items=[AuditLogRead.model_validate(row) for row in rows],
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
    )


# ============================================================================
# Detail Fetch Route
# ============================================================================

@router.get(
    "/organizations/{organization_id}/audit-logs/{audit_log_id}",
    response_model=AuditLogRead,
    summary="Fetch a single audit event",
)
def get_audit_log(
    organization_id: uuid.UUID,
    audit_log_id: uuid.UUID,
    db: Session = Depends(get_read_db),
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
