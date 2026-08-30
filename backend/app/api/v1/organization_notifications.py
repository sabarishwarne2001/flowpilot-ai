"""
Organization-scoped notification feed (ARCH-07 Step 10, §B.11 Option A).
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import RequireOrgMember, get_db
from app.crud import notification as notification_crud
from app.schemas.notification import (
    ORG_NOTIFICATIONS_DEFAULT_PAGE_SIZE,
    ORG_NOTIFICATIONS_MAX_PAGE_SIZE,
    NotificationPage,
    NotificationRead,
    OrganizationNotificationUpdate,
)

router = APIRouter(tags=["Notifications"])


@router.get(
    "/organizations/{organization_id}/notifications",
    response_model=NotificationPage,
    summary="List the caller's organization-scoped notifications",
)
def list_organization_notifications(
    organization_id: uuid.UUID,
    db: Session = Depends(get_db),
    context=Depends(RequireOrgMember),
    is_read: Optional[bool] = Query(
        None, description="Filter by read state. Omit for all."
    ),
    limit: int = Query(
        ORG_NOTIFICATIONS_DEFAULT_PAGE_SIZE, ge=1,
        le=ORG_NOTIFICATIONS_MAX_PAGE_SIZE,
    ),
    offset: int = Query(0, ge=0),
) -> NotificationPage:
    rows, total, unread = notification_crud.list_organization_scoped_for_user(
        db,
        organization_id=context.organization.id,
        user_id=context.user.id,
        is_read=is_read,
        limit=limit,
        offset=offset,
    )
    return NotificationPage(
        items=[NotificationRead.model_validate(row) for row in rows],
        total=total,
        unread_count=unread,
        limit=limit,
        offset=offset,
    )


@router.patch(
    "/organizations/{organization_id}/notifications/{notification_id}",
    response_model=NotificationRead,
    summary="Mark an organization-scoped notification read or unread",
)
def update_organization_notification(
    organization_id: uuid.UUID,
    notification_id: uuid.UUID,
    payload: OrganizationNotificationUpdate,
    db: Session = Depends(get_db),
    context=Depends(RequireOrgMember),
) -> NotificationRead:
    notification = notification_crud.get_organization_scoped_for_user(
        db,
        organization_id=context.organization.id,
        user_id=context.user.id,
        notification_id=notification_id,
    )

    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found.",
        )

    if notification.is_read != payload.is_read:
        notification = notification_crud.update_notification_read_status(
            db,
            notification=notification,
            is_read=payload.is_read,
        )

    return NotificationRead.model_validate(notification)