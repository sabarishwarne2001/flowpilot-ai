"""
Email change endpoints for FlowPilot AI.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field

from app.api import deps
from app.services import email_change_service as ecs

logger = logging.getLogger("app.api.v1.email_change")

router = APIRouter(tags=["Email Change"])


class EmailChangeRequestPayload(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=255)
    new_email: EmailStr


class EmailChangeRequestResponse(BaseModel):
    new_email: str
    expires_at: str


class PendingEmailChangeResponse(BaseModel):
    id: uuid.UUID
    new_email: str
    requested_at: datetime
    expires_at: datetime


class EmailChangeConfirmPayload(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)


class EmailChangeConfirmResponse(BaseModel):
    email: str
    sessions_revoked: bool = True
    detail: str


@router.post(
    "/me/email-change/request",
    response_model=EmailChangeRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request an email address change",
)
async def request_email_change(
    payload: EmailChangeRequestPayload,
    db: deps.DbSession,
    current_user: deps.CurrentUser,
    background_tasks: BackgroundTasks,
) -> Any:
    try:
        request = ecs.request_email_change(
            db,
            user=current_user,
            current_password=payload.current_password,
            new_email=payload.new_email,
            background_tasks=background_tasks,
        )
    except ecs.IncorrectPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        )
    except ecs.EmailUnchangedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    except ecs.EmailAlreadyInUseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )

    return EmailChangeRequestResponse(
        new_email=request.new_email,
        expires_at=request.expires_at.isoformat(),
    )


@router.get(
    "/me/email-change/request",
    response_model=PendingEmailChangeResponse,
    summary="Get the caller's pending email address change",
)
async def get_pending_email_change(
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    request = ecs.get_pending_email_change(db, user=current_user)

    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You have no email change request in progress.",
        )

    return PendingEmailChangeResponse(
        id=request.id,
        new_email=request.new_email,
        requested_at=request.created_at,
        expires_at=request.expires_at,
    )


@router.delete(
    "/me/email-change/request",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Cancel a pending email address change",
)
async def cancel_email_change(
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Response:
    try:
        ecs.cancel_email_change(db, user=current_user)
    except ecs.NoPendingEmailChangeError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/auth/email-change/confirm",
    response_model=EmailChangeConfirmResponse,
    summary="Confirm an email address change",
)
async def confirm_email_change(
    payload: EmailChangeConfirmPayload,
    db: deps.DbSession,
    background_tasks: BackgroundTasks,
) -> Any:
    try:
        user = ecs.confirm_email_change(
            db, token=payload.token, background_tasks=background_tasks
        )
    except ecs.InvalidEmailChangeTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    except ecs.EmailAlreadyInUseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )

    return EmailChangeConfirmResponse(
        email=user.email,
        sessions_revoked=True,
        detail=(
            "Your email address has been updated. You have been signed out "
            "everywhere and will need to sign in again with your new address."
        ),
    )