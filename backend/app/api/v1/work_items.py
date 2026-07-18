"""
Work Items API router endpoints for FlowPilot AI.

Manages secure document file ingestion, size/type validation filters, streaming disk writes, 
and triggers background pipeline execution and manual retry reprocessing tasks.
"""

import uuid
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status, BackgroundTasks, Query
from sqlalchemy.orm import Session
from app import crud
from app import utils
from app.utils import file_utils as utils
from app.api import deps
from app.core.config import settings
from app.models.user import User
from app.schemas.job import JobCreate, JobUpdate, JobResponse
from app.schemas.work_item import WorkItemCreate, WorkItemResponse, WorkItemListResponse, WorkItemStatus, WorkItemUpdate
from app.services import process_document_pipeline, embedding_service
from app.schemas.work_item import WorkItemStatus
from app.services.bm25_service import bm25_service
from app.services.knowledge_base_service import (
    knowledge_base_service,
)
from app.services.document_processor import (
    document_vocabulary_service,
)

from app.services.query_service import (
    query_service,
)

import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Work Items"])


@router.post(
    "/upload", 
    response_model=WorkItemResponse, 
    status_code=status.HTTP_201_CREATED
)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user)
) -> Any:
    """
    Ingests and validates an uploaded document, saves it to disk, and registers its state.
    
    Creates a WorkItem record, instantiates a ProcessingJob, and schedules 
    the asynchronous document intelligence pipeline.
    """
    # 1. Enforce strict MIME-type whitelisting
    if file.content_type not in settings.ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format. Supported MIME-types: {settings.ALLOWED_MIME_TYPES}"
        )

    # 2. Extract and validate file size without loading full contents into RAM
    contents = await file.read()
    file_size = len(contents)
    await file.seek(0)

    if file_size > settings.MAX_UPLOAD_SIZE:
        limit_mb = settings.MAX_UPLOAD_SIZE / (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_PAYLOAD_TOO_LARGE,
            detail=f"File exceeds maximum upload size limit of {limit_mb:.1f} MB."
        )

    # 3. Generate a secure, collision-free storage filename
    stored_filename = utils.generate_secure_filename(file.filename)
    
    # 4. Resolve the safe, verified absolute path for writing on disk
    try:
        safe_path = utils.get_safe_path(stored_filename)
    except ValueError as path_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(path_error)
        )

    # 5. Stream the uploaded file to disk asynchronously in 64KB chunks
    #    Enforce resource closures in a finally block to protect system memory.
    try:
        with open(safe_path, "wb") as buffer:
            while chunk := await file.read(65536):
                buffer.write(chunk)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist uploaded document on local disk storage."
        )
    finally:
        # Guarantee that file handles are always closed to release system resources
        await file.close()

    # 6. Relational database registration - Create WorkItem metadata record
    work_item_in = WorkItemCreate(
        original_filename=file.filename,
        stored_filename=stored_filename,
        file_type=file.content_type,
        file_size=file_size
    )
    work_item = crud.create_work_item(
        db, 
        obj_in=work_item_in, 
        user_id=current_user.id
    )
    
    # 7. Pipeline initialization - Create the companion ProcessingJob record
    job_in = JobCreate(work_item_id=work_item.id)
    job = crud.create_job(db, obj_in=job_in)

    # 8. Dispatch pipeline task to the asynchronous background thread pool
    background_tasks.add_task(
        process_document_pipeline,
        work_item.id,
        job.id
    )

    return work_item


@router.get("", response_model=WorkItemListResponse)
async def list_work_items(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    page: int = Query(1, ge=1),
    pageSize: int = Query(10, ge=1, le=100),
    search: str | None = Query(None),
    status: WorkItemStatus | None = Query(None),
    sortBy: str = Query("created_at"),
    sortOrder: str = Query("desc"),
) -> Any:
    """
    Retrieves a paginated list of WorkItems belonging to the authenticated user.
    """

    skip = (page - 1) * pageSize

    work_items = crud.get_work_items_for_user(
        db,
        user_id=current_user.id,
        skip=skip,
        limit=pageSize,
        search=search,
        status=status,
        sort_by=sortBy,
        sort_order=sortOrder,
    )

    total_items = len(work_items)

    total_pages = (
        (total_items + pageSize - 1) // pageSize
        if total_items > 0
        else 1
    )

    return WorkItemListResponse(
        items=work_items,
        page=page,
        pageSize=pageSize,
        totalItems=total_items,
        totalPages=total_pages,
    )


@router.get("/{work_item_id}", response_model=WorkItemResponse)
async def get_work_item(
    work_item_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user)
) -> Any:
    """
    Retrieves detailed metadata for a specific WorkItem by primary key UUID.
    
    Enforces user isolation; queries for non-owned items return a 404 error.
    """
    work_item = crud.get_work_item_by_id(
        db, 
        work_item_id=work_item_id, 
        user_id=current_user.id
    )
    if work_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work item not found."
        )
    return work_item


@router.post(
    "/{work_item_id}/reprocess", 
    response_model=JobResponse, 
    status_code=status.HTTP_202_ACCEPTED
)
async def reprocess_work_item(
    work_item_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user)
) -> Any:
    """
    Re-triggers the asynchronous processing pipeline for an existing WorkItem.
    
    Enforces user ownership, verifies physical file existence, spawns a new 
    ProcessingJob with an incremented retry counter, and sets the state to QUEUED.
    """
    # 1. Fetch and verify the parent WorkItem exists and is owned by the requesting user
    work_item = crud.get_work_item_by_id(
        db, 
        work_item_id=work_item_id, 
        user_id=current_user.id
    )
    if work_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work item not found."
        )

    # 2. Verify that the physical uploaded file still exists on system disk storage
    try:
        safe_path = Path(utils.get_safe_path(work_item.stored_filename))
        if not safe_path.exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reprocessing is unavailable. The physical document has been removed from disk."
            )
    except ValueError as path_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(path_error)
        )

    # 3. Query existing execution histories to calculate the incremented retry count
    previous_jobs = crud.get_jobs_for_work_item(
        db, 
        work_item_id=work_item_id, 
        user_id=current_user.id
    )
    
    next_retry_count = 0
    if previous_jobs:
        # previous_jobs are sorted by created_at.desc() internally; the first item is the latest run
        next_retry_count = previous_jobs[0].retry_count + 1

    # 4. Reset the WorkItem status back to QUEUED
    crud.update_work_item_state(
        db, 
        db_obj=work_item, 
        obj_in=WorkItemUpdate(status=WorkItemStatus.QUEUED)
    )

    # 5. Create a new ProcessingJob entry record to track this specific run
    job_in = JobCreate(work_item_id=work_item_id)
    new_job = crud.create_job(db, obj_in=job_in)

    # 6. Apply the calculated retry counter to the new run details
    if next_retry_count > 0:
        new_job = crud.update_job(
            db, 
            db_obj=new_job, 
            obj_in=JobUpdate(retry_count=next_retry_count)
        )

    # 7. Dispatch the reprocessing run task to the background thread pool
    background_tasks.add_task(
        process_document_pipeline,
        work_item.id,
        new_job.id
    )

    return new_job


@router.delete(
    "/knowledge-base",
    status_code=status.HTTP_200_OK,
)
async def clear_knowledge_base(
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
):
    """
    Remove every searchable document from
    the AI knowledge base.
    """

    stats = (
        knowledge_base_service.clear_knowledge_base()
    )

    return {

    "message": "Knowledge base cleared successfully.",

    **stats,

}


@router.delete(
    "/{work_item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_work_item(
    work_item_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> None:
    """
    Permanently deletes a work item.
    """

    work_item = crud.get_work_item_by_id(
        db,
        work_item_id=work_item_id,
        user_id=current_user.id,
    )

    if work_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work item not found.",
        )

    file_path = Path(
        utils.get_safe_path(work_item.stored_filename)
    )

    if file_path.exists():
        file_path.unlink()

    embedding_service.delete_chunks(
        work_item.id
    )
    bm25_service.rebuild_index()

    document_vocabulary_service.remove_document(
        work_item.id,
    )

    query_service.update_document_vocabulary(
        document_vocabulary_service.get_expansion_map(),
    )

    logger.info(
        "Vocabulary removed for WorkItem %s.",
        work_item.id,
    )

    crud.delete_work_item(
        db,
        db_obj=work_item,
    )

