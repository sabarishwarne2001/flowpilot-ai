"""
Work Items API router endpoints for FlowPilot AI.
"""

import logging
import uuid
from typing import Any, Iterator, Optional
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
from sqlalchemy import select
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
from app.services import document_intake_service, file_validation_service, job_service

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
async def get_work_item_content(
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
    work_item_ids = db.execute(
        select(WorkItem.id).where(
            WorkItem.workspace_id == context.workspace_id,
            WorkItem.status == WorkItemStatus.COMPLETED,
        )
    ).scalars().all()

    queued = 0
    for work_item_id in work_item_ids:
        job_service.enqueue(
            db,
            job_type="knowledge.reindex",
            organization_id=context.organization_id,
            payload={
                "work_item_id": str(work_item_id),
                "workspace_id": str(context.workspace_id),
            },
            idempotency_key=f"knowledge.reindex:{work_item_id}",
        )
        queued += 1

    db.commit()

    logger.info(
        "AUDIT | KNOWLEDGE_REINDEX_REQUESTED | workspace=%s | user=%s | documents=%d",
        context.workspace_id,
        context.user_id,
        queued,
    )

    return ReindexResponse(
        queued=queued,
        total_documents=len(work_item_ids),
        detail=(
            f"Queued {queued} document(s) for re-embedding. "
            "This runs in the background and may take several minutes."
        ),
    )


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
