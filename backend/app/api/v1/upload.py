"""
Workspace logo upload and authenticated streaming router for FlowPilot AI.

ARCH-06 Step 1b, ARCH-07 Steps 3, 5, 6, 7. ARCH-08 Step 1.
Includes authenticated streaming route GET /workspaces/{workspace_id}/logo with
security headers (nosniff, inline, private cache).
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import deps
from app.api.deps import RequireWorkspaceAdmin, RequireWorkspaceMember, get_db
from app.core.storage import ObjectNotFoundError, get_storage_driver
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.uploaded_file import UploadedFile
from app.schemas.workspace import WorkspaceResponse
from app.services import audit_service

logger = logging.getLogger("app.api.v1.upload")

router = APIRouter(tags=["Upload"])
logo_router = APIRouter(tags=["Workspaces"])

MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_DIMENSION = 2048
LOGO_PURPOSE = "WORKSPACE_LOGO"
OUTPUT_FORMAT = "PNG"
OUTPUT_MIME = "image/png"
LOGO_CACHE_SECONDS = 300


class LogoUploadResponse(BaseModel):
    logo_url: str


def _validate_logo(raw: bytes) -> bytes:
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty upload.")
    if len(raw) > MAX_LOGO_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Logo exceeds {MAX_LOGO_BYTES // (1024 * 1024)} MB.",
        )
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(raw)) as image:
            if max(image.size) > MAX_DIMENSION:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Logo dimensions exceed {MAX_DIMENSION}px.",
                )
            buffer = io.BytesIO()
            image.convert("RGBA").save(buffer, format=OUTPUT_FORMAT, optimize=True)
            return buffer.getvalue()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "File is not a valid image."
        ) from exc


def _current_logo_row(db: Session, *, workspace_id: uuid.UUID) -> Optional[UploadedFile]:
    return (
        db.query(UploadedFile)
        .filter(
            UploadedFile.workspace_id == workspace_id,
            UploadedFile.deleted_at.is_(None),
        )
        .order_by(UploadedFile.created_at.desc())
        .first()
    )


def _stream_and_close(handle):
    try:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        handle.close()


@router.post("/logo", response_model=LogoUploadResponse)
def upload_logo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    context=Depends(RequireWorkspaceAdmin),
) -> LogoUploadResponse:
    workspace = context.workspace
    normalised = _validate_logo(file.file.read())

    driver = get_storage_driver()
    key = f"logos/{uuid.uuid4().hex}.png"
    driver.put(key, normalised, OUTPUT_MIME)

    previous = _current_logo_row(db, workspace_id=workspace.id)
    if previous is not None:
        previous.deleted_at = datetime.now(UTC)

    record = UploadedFile(
        file_path=key,
        original_filename=file.filename or "logo.png",
        mime_type=OUTPUT_MIME,
        file_size=len(normalised),
        checksum_sha256=hashlib.sha256(normalised).hexdigest(),
        owner_id=context.user.id,
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
    )
    db.add(record)
    db.flush()

    workspace.logo_file_id = record.id

    audit_service.record(
        db,
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
        actor_id=context.user.id,
        resource_type=AuditResourceType.UPLOADED_FILE,
        resource_id=record.id,
        action=AuditAction.CREATED,
        details={
            **audit_service.actor_snapshot(context.user),
            "kind": LOGO_PURPOSE,
            "original_filename": file.filename,
            "stored_mime_type": OUTPUT_MIME,
            "size_bytes": len(normalised),
            "storage_key": key,
        },
        **audit_service.context_from_request(request),
    )
    db.commit()
    return LogoUploadResponse(
        logo_url=f"/api/v1/workspaces/{workspace.id}/logo"
    )


@router.delete("/logo", response_model=WorkspaceResponse)
def delete_logo(
    request: Request,
    db: Session = Depends(get_db),
    context=Depends(RequireWorkspaceAdmin),
) -> Any:
    workspace = context.workspace
    record = _current_logo_row(db, workspace_id=workspace.id)
    if record is not None:
        record.deleted_at = datetime.now(UTC)

    workspace.logo_file_id = None

    audit_service.record(
        db,
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
        actor_id=context.user.id,
        resource_type=AuditResourceType.UPLOADED_FILE,
        resource_id=workspace.id,
        action=AuditAction.DELETED,
        details={
            **audit_service.actor_snapshot(context.user),
            "kind": LOGO_PURPOSE,
        },
        **audit_service.context_from_request(request),
    )
    db.commit()
    db.refresh(workspace)
    return workspace


@logo_router.get(
    "/workspaces/{workspace_id}/logo",
    responses={200: {"content": {"image/png": {}}}, 404: {}},
    summary="Stream a workspace's logo",
)
def get_workspace_logo(
    workspace_id: uuid.UUID,
    db: Session = Depends(get_db),
    context=Depends(RequireWorkspaceMember),
    if_none_match: Optional[str] = Header(default=None),
):
    workspace = context.workspace
    record = _current_logo_row(db, workspace_id=workspace.id)
    if record is None or record.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Logo not found.")

    etag = f'"{record.checksum_sha256}"' if record.checksum_sha256 else None
    security_headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
        "Cache-Control": f"private, max-age={LOGO_CACHE_SECONDS}",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if etag:
        security_headers["ETag"] = etag

    if etag and if_none_match and if_none_match.strip() == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=security_headers)

    driver = get_storage_driver()
    try:
        handle = driver.stream(record.file_path)
        size = driver.size(record.file_path)
    except ObjectNotFoundError:
        logger.error(
            "ARCH07_MISSING_OBJECT | uploaded_files.id=%s workspace=%s key=%s",
            record.id, workspace.id, record.file_path,
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Logo not found.") from None

    security_headers["Content-Length"] = str(size)
    return StreamingResponse(
        _stream_and_close(handle),
        media_type=record.mime_type,
        headers=security_headers,
    )