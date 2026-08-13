"""
Workspace logo upload (ARCH-06 Step 1b, ARCH-07 Steps 3, 5, 6).
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import RequireWorkspaceAdmin, get_db
from app.core.storage import get_storage_driver
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.uploaded_file import UploadedFile
from app.schemas.workspace import WorkspaceResponse
from app.services import audit_service, workspace_service

logger = logging.getLogger("app.api.v1.upload")
router = APIRouter(tags=["Upload"])

MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_DIMENSION = 2048
LOGO_PURPOSE = "WORKSPACE_LOGO"
OUTPUT_FORMAT = "PNG"
OUTPUT_MIME = "image/png"


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
            UploadedFile.purpose == LOGO_PURPOSE,
            UploadedFile.deleted_at.is_(None),
        )
        .order_by(UploadedFile.created_at.desc())
        .first()
    )


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
        purpose=LOGO_PURPOSE,
    )
    db.add(record)
    db.flush()

    workspace.logo_file_id = record.id
    workspace.company_logo_url = f"/uploads/{key}"

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
    return LogoUploadResponse(logo_url=workspace.company_logo_url)


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
    workspace.company_logo_url = None

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