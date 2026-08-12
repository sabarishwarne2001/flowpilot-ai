"""
Avatar routes for FlowPilot AI.

ARCH-06 Step 7, §B.7 Option B: avatars are served through an authenticated
route that streams the file with a Content-Type taken from the VALIDATED
stored MIME, plus `X-Content-Type-Options: nosniff` and
`Content-Disposition: inline`.

Registered with no prefix — routes carry their full path, matching `me.py`'s
convention. These are personal-identity routes, not tenant-scoped ones, so
they deliberately do NOT live under WORKSPACE_PREFIX the way `upload.py`'s
logo routes were moved in Step 1b. An avatar belongs to a person across
every tenant they are a member of; scoping it to one workspace would mean a
user needed a different avatar per workspace, which is not the product.

    POST   /me/avatar          upload or replace
    DELETE /me/avatar          remove
    GET    /users/{id}/avatar  stream (authenticated)

WHY THE READ ROUTE IS NOT /me/avatar
---------------------------------------
An avatar is rendered in member directories, audit lines, and mail about
other people — every one of those needs to fetch somebody ELSE's avatar. A
`/me`-only read route would force the frontend back onto a static path for
every case except the user's own, which is exactly the `StaticFiles`
exposure §B.7 exists to close.

WHY §B.7 REPLACES StaticFiles RATHER THAN SUPPLEMENTING IT
-------------------------------------------------------------
`app/main.py` still mounts `/uploads` via `StaticFiles`. A.2.2 explains what
that costs: `StaticFiles` sets Content-Type from the file EXTENSION and
performs no authorization at all, so it cannot express "only members of a
tenant may see this" and cannot be trusted about what it is serving. The
mount is retained for one release for existing logo URLs (§B.7's stated
window, ARCH-07 removes it) — but no avatar is ever reachable through it,
because avatars are written to `uploads/avatars/` under a uuid filename that
appears in no response body. The only path to an avatar's bytes is the route
below.

WHO MAY READ AN AVATAR (E10)
-------------------------------
Any authenticated user may read any other user's avatar **that they share a
tenant with**. Not "any authenticated user", which would let a member of one
organization enumerate and fetch avatars across the whole platform — the
cross-tenant read half of E10.

This is deliberately more permissive than `upload.py`'s workspace-ADMIN gate
on logos and deliberately less permissive than public. An avatar is *meant*
to be seen by colleagues; it is not meant to be a platform-wide directory
photo for strangers.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi import Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api import deps
from app.models.organization import MembershipStatus, OrganizationMember
from app.models.user import User
from app.services import avatar_service

logger = logging.getLogger("app.api.v1.avatar")

router = APIRouter(tags=["Avatar"])


class AvatarResponse(BaseModel):
    """
    Metadata about the stored avatar.

    Deliberately carries NO file path. The client fetches
    `GET /users/{id}/avatar`; publishing a storage key would reintroduce the
    guessable-URL surface §B.7 closes, and `file_path` is an internal storage
    detail that must not become a public contract.
    """

    file_id: uuid.UUID
    mime_type: str
    file_size: int


def _shares_a_tenant(db, *, viewer: User, target_id: uuid.UUID) -> bool:
    """
    True when viewer and target hold an ACTIVE membership in a common
    organization.

    Organization rather than workspace: a colleague in the same company
    should see your avatar in the member directory whether or not you happen
    to share a workspace, and the organization is the boundary the product
    already treats as "people who know each other" (see the member directory
    and every invitation flow).

    ACTIVE on BOTH sides. A removed member must not keep reading the
    directory's faces, and a suspended user should not be visible as an
    active colleague.
    """
    if viewer.id == target_id:
        return True

    viewer_orgs = select(OrganizationMember.organization_id).where(
        OrganizationMember.user_id == viewer.id,
        OrganizationMember.status == MembershipStatus.ACTIVE,
    )
    shared = db.execute(
        select(OrganizationMember.id).where(
            OrganizationMember.user_id == target_id,
            OrganizationMember.status == MembershipStatus.ACTIVE,
            OrganizationMember.organization_id.in_(viewer_orgs),
        ).limit(1)
    ).first()
    return shared is not None


@router.post(
    "/me/avatar",
    response_model=AvatarResponse,
    summary="Upload or replace your avatar",
)
async def upload_avatar(
    db: deps.DbSession,
    current_user: deps.CurrentUser,
    file: UploadFile = File(...),
) -> Any:
    """
    Stores a validated avatar for the signed-in user.

    `file.content_type` is NOT consulted — not here and not in
    `avatar_service`. The bytes are decoded and re-encoded, and the stored
    MIME comes from what the decoder actually found (A.2.2 / E9).

    No workspace or organization is named because none is needed: an avatar
    is a personal file. `set_avatar` records it with `organization_id` and
    `workspace_id` NULL, which `uploaded_files` permits by design.
    """
    raw = await file.read()

    try:
        uploaded = avatar_service.set_avatar(
            db,
            owner=current_user,
            raw=raw,
            original_filename=file.filename or "avatar",
        )
    except avatar_service.QuotaExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        )
    except (
        avatar_service.InvalidImageError,
        avatar_service.ImageTooLargeError,
    ) as exc:
        # 400 for both. The distinction between "not an image" and "too
        # large" is useful to a legitimate user and is preserved in the
        # message, but neither is a 415: the request was well-formed, its
        # content was not acceptable.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )

    return AvatarResponse(
        file_id=uploaded.id,
        mime_type=uploaded.mime_type,
        file_size=uploaded.file_size,
    )


@router.delete(
    "/me/avatar",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Remove your avatar",
)
async def delete_avatar(
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Response:
    """
    Removes the signed-in user's avatar.

    `response_class=Response` is required, not stylistic: FastAPI asserts at
    import time that a 204 route declares no response body, and it infers one
    from the return annotation unless told otherwise. Without it the whole
    application fails to import.
    """
    try:
        avatar_service.clear_avatar(db, owner=current_user)
    except avatar_service.AvatarNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="You have no avatar set."
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/users/{user_id}/avatar",
    summary="Stream a user's avatar",
    response_class=FileResponse,
)
async def stream_avatar(
    user_id: uuid.UUID,
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    """
    Streams an avatar with headers that make its content type non-negotiable.

    THE THREE HEADERS, AND WHY EACH IS LOAD-BEARING
    --------------------------------------------------
    `Content-Type` comes from `uploaded_files.mime_type`, which
    `avatar_service` wrote from a successful Pillow decode — not from the
    file extension (what `StaticFiles` uses) and not from anything the
    uploader sent. It is a fact about the bytes.

    `X-Content-Type-Options: nosniff` stops the browser overriding that
    Content-Type by inspecting content. Without it, a served image the
    browser decides looks like HTML can execute in this origin, which is
    precisely the stored-XSS path A.2.2 describes as "one same-origin
    serving decision away".

    `Content-Disposition: inline` is set explicitly rather than left to
    default, so the response's disposition is a decision this route made.

    404, NOT 403, FOR A CROSS-TENANT READ
    ----------------------------------------
    A caller who shares no tenant with the target gets the same response as
    one asking about a user id that does not exist. A 403 would confirm the
    account exists, turning this route into a membership oracle over
    arbitrary user ids — the same rule `WorkspaceAccessDeniedError` applies
    everywhere else in this codebase.
    """
    target = db.get(User, user_id)

    if target is None or not _shares_a_tenant(
        db, viewer=current_user, target_id=user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found."
        )

    try:
        uploaded = avatar_service.resolve_current(db, owner=target)
        path = avatar_service.resolve_stored_path(uploaded)
    except avatar_service.AvatarNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found."
        )

    if not path.is_file():
        # The row says there is a file and the disk disagrees. Logged loudly
        # because it means a cleanup removed bytes without clearing a
        # pointer, and answered as 404 because from the caller's side there
        # is nothing to serve.
        logger.error(
            "AVATAR_FILE_MISSING | user=%s | file=%s | path=%s",
            user_id, uploaded.id, path,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found."
        )

    return FileResponse(
        path=path,
        media_type=uploaded.mime_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
            # Private, not public: this response is authorized per-caller, so
            # a shared cache must never serve it to a different user.
            "Cache-Control": "private, max-age=300",
        },
    )