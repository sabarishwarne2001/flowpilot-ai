"""
Workspace logo upload router for FlowPilot AI.

ARCH-06 Step 1b — closes A.2.1.

WHAT WAS WRONG
--------------
`DELETE /upload/logo` injected `current_user` and then never read it. There
was no check that the named logo belonged to the caller's workspace, to their
organization, or to any tenant they could see. Logo URLs are returned in
workspace responses to every member, so any authenticated user of any
organization could delete any other tenant's logo by naming its URL. This is
the ARCH-01 authorization defect surviving in a corner nothing re-audited.

Path traversal was *incidentally* contained, because `Path(...).name` discards
directory segments. That was luck rather than design, and the
`startswith("/uploads/logos/")` guard was doing no work the `.name` call did
not already do.

THE ROUTE MOVED, AND THAT IS THE FIX
-------------------------------------
`RequireWorkspaceAdmin` is `RequireWorkspaceRole(WorkspaceRole.ADMIN)`, which
composes `get_workspace_context`, which resolves `workspace_id` from the
REQUEST PATH. The old route had no path parameters at all — it was registered
in router.py's "Global" family alongside health and auth. So this could not be
fixed by editing the handler body: the guard had nothing to bind to.

Both routes therefore move under WORKSPACE_PREFIX:

    POST   /api/v1/workspaces/{workspace_id}/upload/logo
    DELETE /api/v1/workspaces/{workspace_id}/upload/logo

See the companion change to `app/api/v1/router.py`, which moves
`upload.router` out of the Global family and into `_SCOPED`. Both files must
ship together; neither is correct alone.

THREE INDEPENDENT CHECKS, IN THIS ORDER
----------------------------------------
1. `RequireWorkspaceAdmin` — the caller holds an effective ADMIN role on the
   NAMED workspace. A caller with no access gets 404 (never 403, which would
   confirm the workspace exists); a member below ADMIN gets 403.

2. Ownership — the submitted `logo_url` must equal the workspace's stored
   `company_logo_url` EXACTLY. This is the check that stops the cross-tenant
   attack in its remaining form: an attacker who is a legitimate ADMIN of
   their own workspace passes their own `workspace_id` and the victim's
   `logo_url`, clears check 1, and is stopped here. Mismatch returns 404, so
   the response does not confirm whether the named file exists.

   Until Step 5 lands the `uploaded_files` table, the workspace row IS the
   ownership record. That is a deliberate, temporary stand-in and it has a
   known gap, documented at `_resolve_owned_logo_path` below.

3. Containment — the resolved path must sit directly inside UPLOAD_DIR.
   Defence in depth (ARCH-01 PF-2). Check 2 already makes traversal
   unreachable, because a traversal string cannot equal a value this service
   generated, but the unlink is not guarded by the *reason* a check happens to
   hold today.

MAGIC-BYTE VALIDATION IS NOT IN THIS STEP
------------------------------------------
A.2.2 — `file.content_type` is the header the CLIENT sent and nothing reads
the bytes — is real and is unfixed here. It lands in Step 7 with Pillow
verification, dimension bounds, EXIF stripping, and the authenticated
streaming route (§B.7). Step 1 ships authorization only, matching ARCH-05's
Step 1 discipline. Do not let this file's existence read as "uploads are
now safe".
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.api import deps
from app.core.exceptions import WorkspaceAccessDeniedError
from app.models.workspace import Workspace
from app.schemas.workspace import WorkspaceResponse
from app.services import workspace_service

logger = logging.getLogger("app.api.v1.upload")

#: Routes carry only their suffix. Registered under WORKSPACE_PREFIX +
#: "/upload" in router.py, which is where the workspace_id path parameter that
#: RequireWorkspaceAdmin resolves comes from.
router = APIRouter(tags=["Upload"])


UPLOAD_DIR = Path("uploads/logos")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

#: Canonical absolute root, resolved once. Every unlink target is checked
#: against this rather than against the relative UPLOAD_DIR, so a symlink or a
#: `..` segment cannot resolve outside it.
_UPLOAD_ROOT = UPLOAD_DIR.resolve()

#: The public URL prefix that `company_logo_url` values carry. Kept as a
#: constant because it appears in both the value this service writes and the
#: value it validates.
_PUBLIC_PREFIX = "/uploads/logos/"

ALLOWED_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}

MAX_FILE_SIZE = 2 * 1024 * 1024


# ===========================================================================
# Schemas
# ===========================================================================

class LogoUploadResponse(BaseModel):
    """The stored URL, to be written to the workspace by a follow-up PATCH."""

    logo_url: str = Field(
        ...,
        description=(
            "Public path of the stored logo. Not yet attached to the "
            "workspace — PATCH /workspaces/{workspace_id} with this value in "
            "company_logo_url to attach it."
        ),
    )


class DeleteLogoRequest(BaseModel):
    """
    The logo the caller believes is currently attached.

    Retained even though the server already knows the stored value, and
    deliberately so: it makes the request a compare-and-delete rather than a
    blind delete. A stale browser tab holding a logo URL that has since been
    replaced will 404 instead of removing whatever the workspace points at
    now.
    """

    logo_url: str = Field(..., min_length=1, max_length=500)


# ===========================================================================
# Helpers
# ===========================================================================

def _resolve_owned_logo_path(workspace: Workspace, submitted_url: str) -> Path:
    """
    Proves the submitted URL is this workspace's logo, and returns its path.

    Raises WorkspaceAccessDeniedError (-> 404) on any failure, with one
    message for every cause. The caller must not be able to distinguish "this
    workspace has no logo" from "that file belongs to someone else" from
    "that file does not exist" — each of those distinctions is an oracle over
    another tenant's state.

    KNOWN GAP, CLOSING IN STEP 5
    -----------------------------
    A file that has been uploaded but not yet attached via PATCH has no
    ownership record and therefore cannot be deleted through this route. It
    becomes an orphan on disk. That is the accumulated cost A.2.3 already
    describes, and it is bounded by MAX_FILE_SIZE per request with no per-
    tenant cap. The `uploaded_files` table in Step 5 is what records ownership
    at write time and makes orphan collection possible; this function is the
    stand-in until then, not the destination.
    """
    stored = (workspace.company_logo_url or "").strip()
    submitted = submitted_url.strip()

    not_found = WorkspaceAccessDeniedError("Logo not found.")

    if not stored:
        logger.info(
            "UPLOAD_DELETE_REJECTED | workspace=%s | reason=no_logo_attached",
            workspace.id,
        )
        raise not_found

    # Exact equality, not prefix matching or normalization. The stored value
    # was generated by upload_logo below, so any string that differs from it
    # by so much as a character did not come from this workspace's record.
    # This is also why traversal is unreachable: "/uploads/logos/../../etc/x"
    # is not equal to a stored uuid4 filename and never will be.
    if submitted != stored:
        logger.warning(
            "UPLOAD_DELETE_REJECTED | workspace=%s | reason=ownership_mismatch",
            workspace.id,
        )
        raise not_found

    if not stored.startswith(_PUBLIC_PREFIX):
        # The stored value itself is malformed — written by something other
        # than upload_logo. Refuse rather than guess at a filesystem path.
        logger.error(
            "UPLOAD_DELETE_REJECTED | workspace=%s | reason=malformed_stored_url",
            workspace.id,
        )
        raise not_found

    candidate = (UPLOAD_DIR / Path(stored).name).resolve()

    # Containment. Redundant given the equality check above, and kept anyway:
    # the unlink must be safe because of what this function checks, not
    # because of what a caller happens to pass today.
    if candidate.parent != _UPLOAD_ROOT:
        logger.error(
            "UPLOAD_DELETE_REJECTED | workspace=%s | reason=path_escape | path=%s",
            workspace.id,
            candidate,
        )
        raise not_found

    return candidate


# ===========================================================================
# Routes
# ===========================================================================

@router.post(
    "/logo",
    response_model=LogoUploadResponse,
    summary="Upload Workspace Logo",
)
async def upload_logo(
    file: UploadFile = File(...),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
) -> Any:
    """
    Stores a logo image for the addressed workspace.

    ADMIN rather than CONTRIBUTOR: this writes bytes to disk in the tenant's
    name and consumes storage the tenant is not yet metered on, and branding
    is a settings-tier concern — the same tier that already gates
    PATCH /workspaces/{workspace_id}.

    The returned URL is NOT attached to the workspace. The caller follows with
    PATCH /workspaces/{workspace_id} carrying it in `company_logo_url`. That
    two-step shape is pre-existing and is retained here so the frontend change
    stays limited to the path; Step 5 revisits it once uploads have their own
    ownership rows.

    Content-Type is still taken from the client-supplied multipart header
    (A.2.2). See the module docstring — the fix is Step 7, not this step.
    """
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PNG, JPEG and WebP images are allowed.",
        )

    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Logo must be smaller than 2 MB.",
        )

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    extension = ALLOWED_TYPES[file.content_type]
    filename = f"{uuid4()}{extension}"
    destination = UPLOAD_DIR / filename
    destination.write_bytes(content)

    logger.info(
        "AUDIT | WORKSPACE_LOGO_UPLOADED | Workspace: %s | Actor: %s | "
        "File: %s | Bytes: %d",
        context.workspace_id,
        context.user_id,
        filename,
        len(content),
    )

    return LogoUploadResponse(logo_url=f"{_PUBLIC_PREFIX}{filename}")


@router.delete(
    "/logo",
    response_model=WorkspaceResponse,
    summary="Delete Workspace Logo",
)
async def delete_logo(
    payload: DeleteLogoRequest,
    db: deps.DbSession,
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
) -> Any:
    """
    Detaches and deletes the addressed workspace's logo.

    Returns the updated workspace, matching
    DELETE /workspaces/{workspace_id}/logo, so the caller does not need a
    follow-up read to refresh its branding state.

    ORDER: DATABASE FIRST, FILESYSTEM SECOND
    -----------------------------------------
    The pointer is cleared and committed before the file is unlinked. The two
    stores cannot be updated atomically, so one of two failure modes has to be
    chosen, and they are not equally bad:

      - unlink first, commit fails  -> the workspace points at a file that is
        gone. Every member sees a broken image and no one can clear it,
        because the ownership check above needs a stored value to match and
        the file it names no longer exists.

      - commit first, unlink fails  -> an orphan file nobody references. Wastes
        disk. Invisible to users. Collectable by Step 5's `uploaded_files`
        sweep.

    So the unlink failure is logged and swallowed, and the request still
    succeeds. The user asked for the logo to be removed; it has been.
    """
    file_path = _resolve_owned_logo_path(context.workspace, payload.logo_url)

    updated = workspace_service.remove_workspace_logo(
        db,
        workspace=context.workspace,
        effective_role=context.effective_workspace_role,
        actor_id=context.user_id,
    )

    try:
        file_path.unlink(missing_ok=True)
    except OSError as exc:
        # Deliberately not re-raised. See the docstring: the pointer is
        # already gone, which is the part the user can observe.
        logger.error(
            "UPLOAD_UNLINK_FAILED | workspace=%s | path=%s | error=%s",
            context.workspace_id,
            file_path,
            exc,
        )
    else:
        logger.info(
            "AUDIT | WORKSPACE_LOGO_DELETED | Workspace: %s | Actor: %s | "
            "File: %s",
            context.workspace_id,
            context.user_id,
            file_path.name,
        )

    return updated