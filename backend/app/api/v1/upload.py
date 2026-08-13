"""
Workspace logo upload router for FlowPilot AI.

ARCH-06 Step 1b / ARCH-07 Step 3: Converted AUDIT log calls to audit_service.record().
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from app.api import deps
from app.core.exceptions import WorkspaceAccessDeniedError
from app.models.workspace import Workspace
from app.models.audit_log import AuditAction, AuditResourceType
from app.schemas.workspace import WorkspaceResponse
from app.services import audit_service, workspace_service

logger = logging.getLogger("app.api.v1.upload")

router = APIRouter(tags=["Upload"])

UPLOAD_DIR = Path("uploads/logos")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
_UPLOAD_ROOT = UPLOAD_DIR.resolve()
_PUBLIC_PREFIX = "/uploads/logos/"

ALLOWED_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}

MAX_FILE_SIZE = 2 * 1024 * 1024


class LogoUploadResponse(BaseModel):
    logo_url: str = Field(...)


class DeleteLogoRequest(BaseModel):
    logo_url: str = Field(..., min_length=1, max_length=500)


def _resolve_owned_logo_path(workspace: Workspace, submitted_url: str) -> Path:
    stored = (workspace.company_logo_url or "").strip()
    submitted = submitted_url.strip()

    not_found = WorkspaceAccessDeniedError("Logo not found.")

    if not stored or submitted != stored or not stored.startswith(_PUBLIC_PREFIX):
        raise not_found

    candidate = (UPLOAD_DIR / Path(stored).name).resolve()
    if candidate.parent != _UPLOAD_ROOT:
        raise not_found

    return candidate


@router.post(
    "/logo",
    response_model=LogoUploadResponse,
    summary="Upload Workspace Logo",
)
async def upload_logo(
    request: Request,
    file: UploadFile = File(...),
    db: deps.DbSession = None,
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
) -> Any:
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PNG, JPEG and WebP images are allowed.",
        )

    content = await file.read()

    if len(content) > MAX_FILE_SIZE or not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Logo must be smaller than 2 MB and non-empty.",
        )

    extension = ALLOWED_TYPES[file.content_type]
    filename = f"{uuid4()}{extension}"
    destination = UPLOAD_DIR / filename
    destination.write_bytes(content)

    audit_service.record(
        db,
        organization_id=context.organization.id,
        workspace_id=context.workspace_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.UPLOADED_FILE,
        resource_id=context.workspace_id,
        action=AuditAction.CREATED,
        details={
            **audit_service.actor_snapshot(context.user),
            "kind": "WORKSPACE_LOGO",
            "filename": filename,
            "original_filename": file.filename,
            "file_size": len(content),
            "content_type": file.content_type,
        },
        **audit_service.context_from_request(request),
    )

    return LogoUploadResponse(logo_url=f"{_PUBLIC_PREFIX}{filename}")


@router.delete(
    "/logo",
    response_model=WorkspaceResponse,
    summary="Delete Workspace Logo",
)
async def delete_logo(
    request: Request,
    payload: DeleteLogoRequest,
    db: deps.DbSession = None,
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
) -> Any:
    file_path = _resolve_owned_logo_path(context.workspace, payload.logo_url)

    updated = workspace_service.remove_workspace_logo(
        db,
        workspace=context.workspace,
        effective_role=context.effective_workspace_role,
        actor_id=context.user_id,
    )

    audit_service.record(
        db,
        organization_id=context.organization.id,
        workspace_id=context.workspace_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.UPLOADED_FILE,
        resource_id=context.workspace_id,
        action=AuditAction.DELETED,
        details={
            **audit_service.actor_snapshot(context.user),
            "kind": "WORKSPACE_LOGO",
            "filename": file_path.name,
        },
        **audit_service.context_from_request(request),
    )

    try:
        file_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.error("UPLOAD_UNLINK_FAILED | workspace=%s | path=%s | error=%s", context.workspace_id, file_path, exc)

    return updated