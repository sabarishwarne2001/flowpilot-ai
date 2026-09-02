"""
Notifications API router for FlowPilot AI.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.models.notification import Notification
from app.schemas.notification import (
    NotificationResponse,
    NotificationUpdate,
)

logger = logging.getLogger("app.api.v1.notifications")

router = APIRouter(
    tags=["Notifications"],
)


def _get_user_notification(
    *,
    db: Session,
    workspace_id: uuid.UUID,
    notification_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Notification:
    notification = crud.get_notification_by_id(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        notification_id=notification_id,
    )

    if notification is None:
        logger.warning(
            "Notification %s not found for user %s in workspace %s.",
            notification_id,
            user_id,
            workspace_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found.",
        )

    return notification


@router.get(
    "",
    response_model=list[NotificationResponse],
    response_model_exclude_none=True,
    summary="List Notifications",
)
async def list_notifications(
    db: Session = Depends(deps.get_read_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
    is_read: bool | None = Query(default=None, description="Filter by read status."),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
) -> list[NotificationResponse]:
    notifications = crud.list_notifications(
        db,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        is_read=is_read,
        skip=skip,
        limit=limit,
    )

    logger.info(
        "Returned %d notifications for user %s in workspace %s.",
        len(notifications),
        context.user_id,
        context.workspace_id,
    )

    return notifications


@router.get(
    "/{notification_id}",
    response_model=NotificationResponse,
    response_model_exclude_none=True,
    summary="Get Notification",
)
async def get_notification(
    notification_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> NotificationResponse:
    notification = _get_user_notification(
        db=db,
        workspace_id=context.workspace_id,
        notification_id=notification_id,
        user_id=context.user_id,
    )

    logger.info(
        "Returned notification %s for user %s inside workspace %s.",
        notification_id,
        context.user_id,
        context.workspace_id,
    )

    return notification


@router.patch(
    "/{notification_id}",
    response_model=NotificationResponse,
    response_model_exclude_none=True,
    summary="Update Notification",
)
async def update_notification(
    notification_id: uuid.UUID,
    notification_in: NotificationUpdate,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> NotificationResponse:
    notification = _get_user_notification(
        db=db,
        workspace_id=context.workspace_id,
        notification_id=notification_id,
        user_id=context.user_id,
    )

    if notification_in.is_read is not None:
        notification = crud.update_notification_read_status(
            db,
            notification=notification,
            is_read=notification_in.is_read,
        )
        logger.info(
            "Notification %s updated by user %s inside workspace %s (is_read=%s).",
            notification_id,
            context.user_id,
            context.workspace_id,
            notification_in.is_read,
        )

    return notification


@router.post(
    "/mark-all-read",
    summary="Mark All Notifications as Read",
)
async def mark_all_read(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> dict[str, int]:
    updated_count = crud.mark_all_notifications_as_read(
        db,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
    )

    logger.info(
        "User %s marked %d notifications as read in workspace %s.",
        context.user_id,
        updated_count,
        context.workspace_id,
    )

    return {
        "updated_count": updated_count,
    }


@router.delete(
    "/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Notification",
)
async def delete_notification(
    notification_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> Response:
    notification = _get_user_notification(
        db=db,
        workspace_id=context.workspace_id,
        notification_id=notification_id,
        user_id=context.user_id,
    )

    crud.delete_notification(db, notification=notification)

    logger.info(
        "Notification %s deleted by user %s inside workspace %s.",
        notification_id,
        context.user_id,
        context.workspace_id,
    )

    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
    )
