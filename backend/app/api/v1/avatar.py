"""
Avatar routes for FlowPilot AI.

ARCH-06 Step 7, §B.7 Option B: avatars are served through an authenticated
route that streams the file with a Content-Type taken from the VALIDATED
stored MIME, plus `X-Content-Type-Options: nosniff` and
`Content-Disposition: inline`.

Registered with no prefix — routes carry their full path, matching `me.py`'s
convention.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi import Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api import deps
from app.models.organization import MembershipStatus, OrganizationMember
from app.models.user import User
from app.services import avatar_service

logger = logging.getLogger("app.api.v1.avatar")

router = APIRouter(tags=["Avatar"])


class AvatarResponse(BaseModel):
    file_id: uuid.UUID
    mime_type: str
    file_size: int


def _shares_a_tenant(db, *, viewer: User, target_id: uuid.UUID) -> bool:
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
)
async def stream_avatar(
    user_id: uuid.UUID,
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    target = db.get(User, user_id)

    if target is None or not _shares_a_tenant(
        db, viewer=current_user, target_id=user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found."
        )

    try:
        uploaded = avatar_service.resolve_current(db, owner=target)
        stream = avatar_service.open_avatar_stream(uploaded)
    except avatar_service.AvatarNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found."
        )

    return StreamingResponse(
        content=stream,
        media_type=uploaded.mime_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
            "Cache-Control": "private, max-age=300",
        },
    )
