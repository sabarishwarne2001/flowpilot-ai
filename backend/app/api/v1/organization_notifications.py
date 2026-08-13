"""
Organization-scoped notification feed (ARCH-07 Step 10, §B.11 Option A).

    GET /organizations/{organization_id}/notifications
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import RequireOrgMember, get_db
from app.crud import notification as notification_crud
from app.schemas.notification import (
    ORG_NOTIFICATIONS_DEFAULT_PAGE_SIZE,
    ORG_NOTIFICATIONS_MAX_PAGE_SIZE,
    NotificationPage,
    NotificationRead,
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
    """Return the caller's own organization-scoped notifications.

    R9 / E19: Self-filtered to context.user.id. Org members read their own rows;
    admins receive no elevated cross-user view here.
    """
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