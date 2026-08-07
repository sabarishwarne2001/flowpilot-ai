"""
Work Items API router endpoints for FlowPilot AI.
"""

import uuid
from pathlib import Path
from typing import Any
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
    BackgroundTasks,
    Query,
)
from sqlalchemy.orm import Session

from app import crud
from app import utils
from app.api import deps
from app.core.config import settings
from app.models.workspace import WorkspaceRole
from app.schemas.job import JobResponse
from app.schemas.work_item import (
    WorkItemCreate,
    WorkItemResponse,
    WorkItemListResponse,
    WorkItemStatus,
)
from app.services import process_document_pipeline
from app.core.exceptions import WorkspacePermissionDeniedError

router = APIRouter(tags=["Work Items"])


@router.post("", response_model=WorkItemResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> WorkItemResponse:
    if file.content_type not in settings.ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format. Supported: {settings.ALLOWED_MIME_TYPES}"
        )

    contents = await file.read()
    file_size = len(contents)
    await file.seek(0)

    # Resolve document settings limit dynamically via workspace scope
    doc_settings = crud.get_document_settings(db, workspace_id=context.workspace_id)
    limit_bytes = (doc_settings.max_upload_size if doc_settings else 50) * 1024 * 1024
    if file_size > limit_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_PAYLOAD_TOO_LARGE,
            detail=f"File exceeds maximum upload size limit."
        )

    stored_filename = utils.generate_secure_filename(file.filename)
    try:
        safe_path = utils.get_safe_path(stored_filename)
    except ValueError as path_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(path_error)
        )

    try:
        with open(safe_path, "wb") as buffer:
            while chunk := await file.read(65536):
                buffer.write(chunk)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist uploaded document on disk."
        )
    finally:
        await file.close()

    obj_in = WorkItemCreate(
        original_filename=file.filename,
        stored_filename=stored_filename,
        file_type=file.content_type,
        file_size=file_size
    )

    work_item = crud.create_work_item(
        db,
        workspace_id=context.workspace_id,
        created_by_user_id=context.user_id,
        obj_in=obj_in,
    )
    
    job_in = crud.JobCreate(work_item_id=work_item.id)
    job = crud.create_job(db, obj_in=job_in)

    background_tasks.add_task(
        process_document_pipeline,
        work_item.id,
        job.id
    )

    return work_item


@router.get("", response_model=WorkItemListResponse)
async def list_work_items(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: str | None = Query(None),
    status_filter: WorkItemStatus | None = Query(None, alias="status"),
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
    return WorkItemListResponse(items=items, total=total, skip=skip, limit=limit)


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


@router.post("/{work_item_id}/reprocess", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def reprocess_work_item(
    work_item_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> Any:
    work_item = crud.get_work_item(
        db, workspace_id=context.workspace_id, work_item_id=work_item_id
    )
    if work_item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Work item not found.")

    try:
        safe_path = Path(utils.get_safe_path(work_item.stored_filename))
        if not safe_path.exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reprocessing unavailable. Physical document removed from disk."
            )
    except ValueError as path_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(path_error)
        )

    previous_jobs = crud.get_jobs_for_work_item(
        db, workspace_id=context.workspace_id, work_item_id=work_item_id
    )
    next_retry_count = previous_jobs[0].retry_count + 1 if previous_jobs else 0

    crud.update_work_item_state(
        db, db_obj=work_item, obj_in=crud.WorkItemUpdate(status=WorkItemStatus.QUEUED)
    )

    job_in = crud.JobCreate(work_item_id=work_item_id)
    new_job = crud.create_job(db, obj_in=job_in)

    if next_retry_count > 0:
        new_job = crud.update_job(
            db, db_obj=new_job, obj_in=crud.JobUpdate(retry_count=next_retry_count)
        )

    background_tasks.add_task(
        process_document_pipeline,
        work_item.id,
        new_job.id
    )

    return new_job


@router.delete("/{work_item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_work_item(
    work_item_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> None:
    work_item = crud.get_work_item(
        db, workspace_id=context.workspace_id, work_item_id=work_item_id
    )
    if work_item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Work item not found.")

    if (context.role is not WorkspaceRole.ADMIN
            and work_item.created_by_user_id != context.user_id):
        raise WorkspacePermissionDeniedError(
            "Only the uploader or a workspace administrator may delete this document."
        )

    crud.delete_work_item(db, db_obj=work_item)