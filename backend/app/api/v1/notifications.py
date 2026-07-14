"""
Notifications API router for FlowPilot AI.

Provides authenticated CRUD endpoints for managing in-app notifications
while enforcing strict user ownership on every operation.
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
from app.models.user import User
from app.schemas.notification import (
    NotificationResponse,
    NotificationUpdate,
)

logger = logging.getLogger(
    "app.api.v1.notifications"
)

router = APIRouter(
    tags=["Notifications"],
)

def _get_user_notification(
    *,
    db: Session,
    notification_id: uuid.UUID,
    current_user: User,
) -> Notification:
    """
    Retrieve a notification owned by the authenticated user.

    Raises:
        HTTPException:
            404 if the notification does not exist or is not
            owned by the current user.
    """

    notification = crud.get_notification_by_id(
        db,
        notification_id=notification_id,
        user_id=current_user.id,
    )

    if notification is None:

        logger.warning(
            "Notification %s not found for user %s.",
            notification_id,
            current_user.id,
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
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
    is_read: bool | None = Query(
        default=None,
        description="Filter by read status.",
    ),
    skip: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=100,
    ),
) -> list[NotificationResponse]:
    """
    Retrieve notifications belonging to the authenticated user.

    Results are ordered by newest first and support optional
    filtering by read status.
    """

    notifications = crud.get_notifications_for_user(
        db,
        user_id=current_user.id,
        is_read=is_read,
        skip=skip,
        limit=limit,
    )

    logger.info(
        "Returned %d notifications for user %s.",
        len(notifications),
        current_user.id,
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
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
) -> NotificationResponse:
    """
    Retrieve a single notification belonging to the
    authenticated user.
    """

    notification = _get_user_notification(
        db=db,
        notification_id=notification_id,
        current_user=current_user,
    )

    logger.info(
        "Returned notification %s for user %s.",
        notification_id,
        current_user.id,
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
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
) -> NotificationResponse:
    """
    Update the read/unread state of a notification
    owned by the authenticated user.
    """

    notification = _get_user_notification(
        db=db,
        notification_id=notification_id,
        current_user=current_user,
    )

    #
    # Only update when a value is provided.
    #
    if notification_in.is_read is not None:

        notification = (
            crud.update_notification_read_status(
                db,
                notification=notification,
                is_read=notification_in.is_read,
            )
        )

        logger.info(
            "Notification %s updated by user %s (is_read=%s).",
            notification_id,
            current_user.id,
            notification_in.is_read,
        )

    else:

        logger.info(
            "Notification %s update requested by user %s with no changes.",
            notification_id,
            current_user.id,
        )

    return notification


@router.post(
    "/mark-all-read",
    summary="Mark All Notifications as Read",
)
async def mark_all_read(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
) -> dict[str, int]:
    """
    Mark every unread notification belonging to the
    authenticated user as read.
    """

    updated_count = (
        crud.mark_all_notifications_as_read(
            db,
            user_id=current_user.id,
        )
    )

    logger.info(
        "User %s marked %d notifications as read.",
        current_user.id,
        updated_count,
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
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
) ->Response:
    """
    Delete a notification belonging to the authenticated user.
    """

    notification = _get_user_notification(
        db=db,
        notification_id=notification_id,
        current_user=current_user,
    )

    crud.delete_notification(
        db,
        notification=notification,
    )

    logger.info(
        "Notification %s deleted by user %s.",
        notification_id,
        current_user.id,
    )

    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
    )