"""
Work Items API router endpoints for FlowPilot AI.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Iterator, Literal, Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.core.config import settings
from app.core.exceptions import WorkspacePermissionDeniedError
from app.core.storage import (
    DEFAULT_CHUNK_SIZE,
    ObjectNotFoundError,
    TenantKeyError,
    assert_key_belongs_to,
    get_storage_driver,
)
from app.models.work_item import WorkItem
from app.models.workspace import WorkspaceRole
from app.schemas.job import JobResponse
from app.schemas.work_item import (
    WorkItemListResponse,
    WorkItemResponse,
    WorkItemStatus,
    WorkItemUpdate,
)
from app.services import document_intake_service, file_validation_service, job_service, plan_admission

logger = logging.getLogger("app.api.v1.work_items")

router = APIRouter(tags=["Work Items"])


INLINE_RENDERABLE_MIMES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
    }
)


class ReindexResponse(BaseModel):
    queued: int
    total_documents: int
    detail: str

    model_config = ConfigDict(protected_namespaces=())


class ReindexRunStatus(BaseModel):
    """One click of "Reindex knowledge base": the jobs it queued and how far they got."""

    requested_at: datetime
    #: When the last of its jobs settled; null while any is still waiting or running.
    finished_at: Optional[datetime]
    total: int
    #: Waiting, running, or waiting to be retried after an error.
    waiting: int
    completed: int
    #: Gave up (dead after its retries) or finished without re-embedding (budget, unreadable text).
    failed: int


class ReindexStatusResponse(BaseModel):
    state: Literal["idle", "running"]
    #: Every reindex job of this workspace still waiting or running, whichever click queued it.
    active_jobs: int
    #: The most recent click, or null when this workspace was never reindexed.
    latest: Optional[ReindexRunStatus]
    #: When the most recent run that has fully settled finished.
    last_completed_at: Optional[datetime]


# F-218. A plain `def`: spooling, validation (PDF probe, EXIF scrub), the
# object-storage write and the commit all block, so the route runs in the
# threadpool instead of holding the event loop for the whole upload.
@router.post("", response_model=WorkItemResponse, status_code=status.HTTP_201_CREATED)
def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> WorkItemResponse:
    # 1. Resolve dynamic workspace-scoped size limit
    doc_settings = crud.get_document_settings(db, workspace_id=context.workspace_id)
    limit_mb = doc_settings.max_upload_size if doc_settings else (settings.MAX_UPLOAD_SIZE // (1024 * 1024))
    limit_bytes = limit_mb * 1024 * 1024
    # Campaign session 1: never more than the plan's file size, read no further than it.
    plan_bytes = plan_admission.max_upload_bytes(db, organization_id=context.organization_id)

    # 2. Bounded chunked spooling
    try:
        spool, total_size = file_validation_service.spool_upload_file(
            file, max_bytes=min(limit_bytes, plan_bytes)
        )
    except file_validation_service.FileValidationError as exc:
        if exc.reason is file_validation_service.RejectionReason.TOO_LARGE and plan_bytes < limit_bytes:
            plan_admission.assert_file_fits_plan(
                db, organization_id=context.organization_id, size_bytes=plan_bytes + 1, page_count=None
            )
        raise HTTPException(
            status_code=(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
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
            # HARDENING-T2:D13. The workspace setting narrows the platform list.
            allowed_mimes=file_validation_service.workspace_allowed_mimes(
                db, context.workspace_id, settings.ALLOWED_MIME_TYPES
            ),
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
def list_work_items(
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
        search=search,
        status=status_filter,
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
def get_work_item(
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


@router.get(
    "/{work_item_id}/content",
    summary="Document Bytes",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "Document bytes."},
        206: {"description": "Partial content."},
        404: {"description": "Work item absent, not yours, or object missing."},
        416: {"description": "Requested range not satisfiable."},
    },
)
def get_work_item_content(
    work_item_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
    range_header: Optional[str] = Header(default=None, alias="Range"),
) -> Response:
    work_item = crud.get_work_item(
        db, workspace_id=context.workspace_id, work_item_id=work_item_id
    )
    if work_item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Work item not found.")

    storage_key = work_item.stored_filename
    if not storage_key:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "This document has no stored object.",
        )

    # Double tenant check
    try:
        assert_key_belongs_to(storage_key, context.organization_id)
    except TenantKeyError:
        logger.critical(
            "work_item.content_tenant_key_mismatch",
            extra={
                "work_item_id": str(work_item_id),
                "workspace_id": str(context.workspace_id),
                "organization_id": str(context.organization_id),
            },
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Work item not found.")
    except ValueError:
        logger.error(
            "work_item.content_unparseable_key",
            extra={"work_item_id": str(work_item_id)},
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Work item not found.")

    driver = get_storage_driver()

    try:
        total_size = driver.size(storage_key)
    except (ObjectNotFoundError, FileNotFoundError):
        logger.warning(
            "work_item.content_object_missing",
            extra={"work_item_id": str(work_item_id)},
        )
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "The stored document object is missing from storage.",
        )

    declared_mime = work_item.file_type or "application/octet-stream"
    inline_ok = declared_mime in INLINE_RENDERABLE_MIMES

    safe_name = (work_item.original_filename or "document").replace('"', "").replace("\\", "")
    ascii_name = safe_name.encode("ascii", "ignore").decode("ascii") or "document"
    disposition = (
        f"{'inline' if inline_ok else 'attachment'}; "
        f'filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(safe_name)}"
    )

    base_headers = {
        "Content-Disposition": disposition,
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=300, no-transform",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; object-src 'none'; sandbox",
    }

    parsed_range = _parse_single_range(range_header, total_size)

    if parsed_range is _RANGE_UNSATISFIABLE:
        return Response(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={**base_headers, "Content-Range": f"bytes */{total_size}"},
        )

    if parsed_range is None:
        return StreamingResponse(
            driver.iter_chunks(storage_key, chunk_size=DEFAULT_CHUNK_SIZE),
            media_type=declared_mime,
            headers={**base_headers, "Content-Length": str(total_size)},
        )

    start, end = parsed_range
    return StreamingResponse(
        _iter_range(driver, storage_key, start=start, end=end),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=declared_mime,
        headers={
            **base_headers,
            "Content-Range": f"bytes {start}-{end}/{total_size}",
            "Content-Length": str(end - start + 1),
        },
    )


_RANGE_UNSATISFIABLE = object()


def _parse_single_range(header: Optional[str], total_size: int) -> Any:
    if not header or total_size == 0:
        return None

    header = header.strip()
    if not header.lower().startswith("bytes="):
        return None

    spec = header[len("bytes="):].strip()
    if "," in spec:
        return None

    start_raw, _, end_raw = spec.partition("-")
    start_raw = start_raw.strip()
    end_raw = end_raw.strip()

    try:
        if not start_raw:
            if not end_raw:
                return None
            suffix = int(end_raw)
            if suffix <= 0:
                return _RANGE_UNSATISFIABLE
            start = max(total_size - suffix, 0)
            end = total_size - 1
        else:
            start = int(start_raw)
            end = int(end_raw) if end_raw else total_size - 1
    except ValueError:
        return None

    if start < 0 or start >= total_size:
        return _RANGE_UNSATISFIABLE

    end = min(end, total_size - 1)
    if end < start:
        return _RANGE_UNSATISFIABLE

    return (start, end)


def _iter_range(driver: Any, key: str, *, start: int, end: int) -> Iterator[bytes]:
    handle = driver.stream(key)
    try:
        remaining_prefix = start

        if getattr(handle, "seekable", lambda: False)():
            handle.seek(start)
            remaining_prefix = 0

        while remaining_prefix > 0:
            skip = handle.read(min(remaining_prefix, DEFAULT_CHUNK_SIZE))
            if not skip:
                return
            remaining_prefix -= len(skip)

        remaining = end - start + 1
        while remaining > 0:
            chunk = handle.read(min(remaining, DEFAULT_CHUNK_SIZE))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk
    finally:
        close = getattr(handle, "close", None)
        if callable(close):
            close()


@router.post("/{work_item_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
def reprocess_work_item(
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

    # Campaign session 1: OCR runs (and is charged) again; refuse now, with the
    # reason, rather than leave the document blocked in the worker.
    plan_admission.assert_reprocess_admitted(
        db, organization_id=context.organization_id, page_count=work_item.page_count
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
    # ARCH37-S1:work-item-reprocessed
    from app.services import outbox_service

    outbox_service.emit_trigger(
        db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        event_type="trigger.work_item.reprocessed",
        resource_id=work_item.id,
        payload={
            "work_item_id": str(work_item.id),
            "original_filename": work_item.original_filename,
            "requested_by_user_id": str(context.user_id),
        },
        idempotency_key=f"trigger.work_item.reprocessed:{work_item.id}:{job.id}",
    )
    db.commit()

    return {
        "work_item_id": str(work_item.id),
        "job_id": str(job.id),
        "status": "QUEUED",
    }


@router.post(
    "/knowledge-base/reindex",
    response_model=ReindexResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-embed this workspace's documents",
)
def reindex_knowledge_base(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
) -> ReindexResponse:
    from app.models.job import Job, JobStatus

    work_item_ids = db.execute(
        select(WorkItem.id).where(
            WorkItem.workspace_id == context.workspace_id,
            WorkItem.status == WorkItemStatus.COMPLETED,
        )
    ).scalars().all()

    # A document whose reindex is still waiting or running is not queued again (a double click),
    # but one whose earlier reindex finished or died is. The key used to be one per document,
    # forever: after the first run every click answered "Queued N" and queued nothing.
    waiting = {
        str(payload.get("work_item_id"))
        for payload in db.execute(
            select(Job.payload).where(
                Job.job_type == "knowledge.reindex",
                Job.organization_id == context.organization_id,
                Job.status.in_((JobStatus.PENDING, JobStatus.CLAIMED, JobStatus.FAILED)),
            )
        ).scalars()
        if isinstance(payload, dict)
    }
    request_id = uuid.uuid4().hex

    queued = 0
    for work_item_id in work_item_ids:
        if str(work_item_id) in waiting:
            continue
        job_service.enqueue(
            db,
            job_type="knowledge.reindex",
            organization_id=context.organization_id,
            payload={
                "work_item_id": str(work_item_id),
                "workspace_id": str(context.workspace_id),
                # Groups the jobs of one click, so the status route can report that run.
                "batch_id": request_id,
            },
            idempotency_key=f"knowledge.reindex:{work_item_id}:{request_id}",
        )
        queued += 1

    db.commit()

    logger.info(
        "AUDIT | KNOWLEDGE_REINDEX_REQUESTED | workspace=%s | user=%s | documents=%d | already_waiting=%d",
        context.workspace_id,
        context.user_id,
        queued,
        len(work_item_ids) - queued,
    )

    if queued or not work_item_ids:
        detail = (
            f"Queued {queued} document(s) for re-embedding. "
            "This runs in the background and may take several minutes."
        )
    else:
        detail = "Every document is already waiting to be re-embedded. Nothing new was queued."
    return ReindexResponse(
        queued=queued,
        total_documents=len(work_item_ids),
        detail=detail,
    )


#: Runs read per status request. A run older than this many clicks that is still
#: waiting is counted in `active_jobs` (which has no limit) but not in a run's figures.
_REINDEX_RUNS_READ = 50


@router.get(
    "/knowledge-base/reindex/status",
    response_model=ReindexStatusResponse,
    summary="Progress of this workspace's knowledge base reindex",
)
def reindex_knowledge_base_status(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
) -> ReindexStatusResponse:
    """F-141. What the Settings card polls while a reindex runs.

    A run is the set of jobs one click queued (`payload.batch_id`; jobs queued
    before the id existed are grouped by the request id in their idempotency key).
    A job counts as completed when the handler re-embedded the document or found
    it gone, and as failed when it died after its retries or finished without
    re-embedding (the platform backfill budget, text that could not be read).
    """
    from app.models.job import Job, JobStatus

    active_statuses = (JobStatus.PENDING, JobStatus.CLAIMED, JobStatus.FAILED)
    outcome = Job.result["outcome"].astext
    run_id = func.coalesce(
        Job.payload["batch_id"].astext, func.split_part(Job.idempotency_key, ":", 3)
    )
    of_this_workspace = (
        Job.job_type == "knowledge.reindex",
        Job.organization_id == context.organization_id,
        Job.payload["workspace_id"].astext == str(context.workspace_id),
    )

    active_jobs = int(
        db.execute(
            select(func.count())
            .select_from(Job)
            .where(*of_this_workspace, Job.status.in_(active_statuses))
        ).scalar_one()
    )

    waiting = func.count().filter(Job.status.in_(active_statuses))
    completed = func.count().filter(
        Job.status == JobStatus.SUCCEEDED,
        func.coalesce(outcome, "COMPLETED").in_(("COMPLETED", "SKIPPED")),
    )
    rows = db.execute(
        select(
            run_id.label("run_id"),
            func.min(Job.created_at).label("requested_at"),
            func.max(func.coalesce(Job.succeeded_at, Job.updated_at)).label("settled_at"),
            func.count().label("total"),
            waiting.label("waiting"),
            completed.label("completed"),
        )
        .where(*of_this_workspace)
        .group_by(run_id)
        .order_by(func.min(Job.created_at).desc())
        .limit(_REINDEX_RUNS_READ)
    ).all()

    runs = [
        ReindexRunStatus(
            requested_at=row.requested_at,
            finished_at=None if row.waiting else row.settled_at,
            total=row.total,
            waiting=row.waiting,
            completed=row.completed,
            failed=row.total - row.waiting - row.completed,
        )
        for row in rows
    ]
    last_finished = next((run for run in runs if run.finished_at is not None), None)
    return ReindexStatusResponse(
        state="running" if active_jobs else "idle",
        active_jobs=active_jobs,
        latest=runs[0] if runs else None,
        last_completed_at=last_finished.finished_at if last_finished else None,
    )


@router.delete(
    "/{work_item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_work_item(
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

    # F-108: a legal hold (or the organization's retention floor) refuses the
    # single delete exactly as it refuses the bulk one.
    from app.services.ingestion import retention_service

    block = retention_service.blocking_reasons(
        db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        work_items=[work_item],
    ).get(work_item.id)
    if block is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": block.code, "message": block.reason},
        )

    crud.delete_work_item(db, db_obj=work_item)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
