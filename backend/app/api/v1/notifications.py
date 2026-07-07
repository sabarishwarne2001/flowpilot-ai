"""
Notifications API router endpoints for FlowPilot AI.

Exposes CRUD endpoints for managing in-app alert notification cards, 
enforcing strict multi-tenant boundary checks on every transaction.
"""

import logging
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, Response
from sqlalchemy.orm import Session
from app import crud
from app.api import deps
from app.models.user import User
from app.schemas.notification import NotificationUpdate, NotificationResponse

router = APIRouter(tags=["Notifications"])
logger = logging.getLogger("app.api.v1.notifications")


@router.get(
    "", 
    response_model=list[NotificationResponse],
    summary="List all Notifications",
    response_description="A paginated list of in-app alert Notification cards owned by the user."
)
async def list_notifications(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    is_read: bool | None = Query(None, description="Optional filter to select only read or unread alerts."),
    skip: int = Query(0, ge=0, description="The number of alerts to skip for pagination."),
    limit: int = Query(100, ge=1, le=100, description="The maximum number of alerts to return.")
) -> Any:
    """
    Retrieves a paginated list of all in-app Notification alerts for the authenticated user.
    """
    notifications = crud.get_notifications_for_user(
        db, 
        user_id=current_user.id, 
        is_read=is_read, 
        skip=skip, 
        limit=limit
    )
    return notifications


@router.patch(
    "/{notification_id}", 
    response_model=NotificationResponse,
    summary="Update a Notification read status",
    response_description="The updated Notification."
)
async def update_notification(
    notification_id: uuid.UUID,
    notification_in: NotificationUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user)
) -> Any:
    """
    Modifies the read/unread state of a specific in-app Notification card.
    
    Enforces user isolation; modifications on non-owned notifications return a 404 error.
    """
    notification = crud.get_notification_by_id(
        db, 
        notification_id=notification_id, 
        user_id=current_user.id
    )
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found."
        )
    
    # If the is_read parameter is provided inside the payload, update its status
    if notification_in.is_read is not None:
        notification = crud.update_notification_read_status(
            db, 
            db_obj=notification, 
            is_read=notification_in.is_read
        )
        logger.info(
            f"User {current_user.id} updated Notification [ID: {notification_id}] "
            f"read status to: {notification_in.is_read}"
        )
        
    return notification


@router.post(
    "/mark-all-read", 
    summary="Mark all Notifications as read",
    response_description="Returns the total count of notification cards modified."
)
async def mark_all_read(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user)
) -> Any:
    """
    Marks all unread Notification cards belonging to the authenticated user as read.
    
    Triggers an optimized UPDATE statement to modify multiple records in a single transactional run.
    """
    updated_count = crud.mark_all_notifications_as_read(db, user_id=current_user.id)
    logger.info(
        f"User {current_user.id} marked all unread notifications as read "
        f"[Modified Count: {updated_count}]"
    )
    return {"updated_count": updated_count}


@router.delete(
    "/{notification_id}", 
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a Notification",
    response_description="Empty response indicating successful deletion."
)
async def delete_notification(
    notification_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user)
) -> Response:
    """
    Removes a specific Notification card from PostgreSQL.
    
    Enforces user isolation; deletions of non-owned notifications return a 404 error.
    """
    notification = crud.get_notification_by_id(
        db, 
        notification_id=notification_id, 
        user_id=current_user.id
    )
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found."
        )
    
    crud.delete_notification(db, db_obj=notification)
    logger.info(f"User {current_user.id} deleted Notification [ID: {notification_id}]")
    return Response(status_code=status.HTTP_204_NO_CONTENT)