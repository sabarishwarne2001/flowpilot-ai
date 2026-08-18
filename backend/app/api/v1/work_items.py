"""
Work Items API router endpoints for FlowPilot AI.
"""

import uuid
from typing import Any, Optional
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.core.config import settings
from app.core.exceptions import WorkspacePermissionDeniedError
from app.core.storage import get_storage_driver
from app.models.workspace import WorkspaceRole
from app.schemas.job import JobResponse
from app.schemas.work_item import (
    WorkItemListResponse,
    WorkItemResponse,
    WorkItemStatus,
    WorkItemUpdate,
)
from app.services import document_intake_service, file_validation_service, job_service

router = APIRouter(tags=["Work Items"])


@router.post("", response_model=WorkItemResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> WorkItemResponse:
    # 1. Resolve dynamic workspace-scoped size limit
    doc_settings = crud.get_document_settings(db, workspace_id=context.workspace_id)
    limit_mb = doc_settings.max_upload_size if doc_settings else (settings.MAX_UPLOAD_SIZE // (1024 * 1024))
    limit_bytes = limit_mb * 1024 * 1024

    # 2. Bounded chunked spooling
    try:
        spool, total_size = await file_validation_service.spool_upload_file(
            file, max_bytes=limit_bytes
        )
    except file_validation_service.FileValidationError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_413_PAYLOAD_TOO_LARGE
                if exc.reason is file_validation_service.RejectionReason.TOO_LARGE
                else status.HTTP_400_BAD_REQUEST
            ),
            detail=str(exc),
        )

    filename = file.filename or "uploaded_document"

    # 3. Pre-durable validation pipeline (magic bytes, MIME agreement, EXIF scrub, page probe)
    try:
        validated = file_validation_service.validate_spooled(
            spool,
            total_size,
            declared_mime=file.content_type,
            original_filename=filename,
            allowed_mimes=settings.ALLOWED_MIME_TYPES,
            max_pages=settings.MAX_DOCUMENT_PAGES,
            scrub_metadata=settings.SCRUB_UPLOAD_METADATA,
        )
    except file_validation_service.FileValidationError as exc:
        quarantine_key = document_intake_service.quarantine(
            spool,
            organization_id=context.organization_id,
            error=exc,
            original_filename=filename,
        )
        document_intake_service.record_rejection(
            db,
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            error=exc,
            original_filename=filename,
            quarantine_key=quarantine_key,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    # 4. Make durable in object storage and commit metadata + job in one transaction
    with validated:
        intake_result = document_intake_service.ingest_validated(
            db,
            validated,
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            uploader_id=context.user_id,
            enqueue_extraction=True,
        )
        db.commit()
        db.refresh(intake_result.work_item)
        return intake_result.work_item


@router.get("", response_model=WorkItemListResponse)
async def list_work_items(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None),
    status_filter: Optional[WorkItemStatus] = Query(None, alias="status"),
    mine_only: bool = Query(False),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
) -> WorkItemListResponse:
    items = crud.list_work_items(
        db,
        workspace_id=context.workspace_id,
        skip=skip,
        limit=limit,
        search=search,
        status=status_filter,
        created_by_user_id=context.user_id if mine_only else None,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    total = crud.count_work_items(
        db,
        workspace_id=context.workspace_id,
        created_by_user_id=context.user_id if mine_only else None,
    )
    page = (skip // limit) + 1
    total_pages = (total + limit - 1) // limit if total > 0 else 1

    return WorkItemListResponse(
        items=items,
        page=page,
        pageSize=limit,
        totalItems=total,
        totalPages=total_pages,
    )


@router.get("/{work_item_id}", response_model=WorkItemResponse)
async def get_work_item(
    work_item_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> WorkItemResponse:
    work_item = crud.get_work_item(
        db, workspace_id=context.workspace_id, work_item_id=work_item_id
    )
    if work_item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Work item not found.")
    return work_item


@router.post("/{work_item_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
async def reprocess_work_item(
    work_item_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> dict[str, Any]:
    work_item = crud.get_work_item(
        db, workspace_id=context.workspace_id, work_item_id=work_item_id
    )
    if work_item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Work item not found.")

    storage_driver = get_storage_driver()
    if not storage_driver.exists(work_item.stored_filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reprocessing unavailable. Stored document object missing from storage.",
        )

    crud.update_work_item_state(
        db, db_obj=work_item, obj_in=WorkItemUpdate(status=WorkItemStatus.QUEUED)
    )

    job = job_service.enqueue(
        db,
        job_type=document_intake_service.OCR_JOB_TYPE,
        payload={
            "work_item_id": str(work_item.id),
            "uploaded_file_id": str(work_item.uploaded_file_id) if work_item.uploaded_file_id else None,
            "storage_key": work_item.stored_filename,
            "mime_type": work_item.file_type,
            "page_count": work_item.page_count,
        },
        organization_id=context.organization_id,
        idempotency_key=f"{document_intake_service.OCR_JOB_TYPE}:{work_item.id}:{uuid.uuid4().hex[:8]}",
        max_attempts=settings.OCR_JOB_MAX_ATTEMPTS,
    )
    db.commit()

    return {
        "work_item_id": str(work_item.id),
        "job_id": str(job.id),
        "status": "QUEUED",
    }


@router.delete(
    "/{work_item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_work_item(
    work_item_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> Response:
    work_item = crud.get_work_item(
        db, workspace_id=context.workspace_id, work_item_id=work_item_id
    )
    if work_item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Work item not found.")

    if (
        context.role is not WorkspaceRole.ADMIN
        and work_item.created_by_user_id != context.user_id
    ):
        raise WorkspacePermissionDeniedError(
            "Only the uploader or a workspace administrator may delete this document."
        )

    crud.delete_work_item(db, db_obj=work_item)
    return Response(status_code=status.HTTP_204_NO_CONTENT)