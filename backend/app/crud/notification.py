"""
Database operations layer for Notifications in FlowPilot AI.

ARCH-07 Step 10: Preserves all 7 existing CRUD functions and adds
list_organization_scoped_for_user.
"""

import uuid
from typing import Sequence
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from app.models.notification import Notification, NotificationStatus
from app.schemas.notification import NotificationCreate


def create_notification(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    notification_in: NotificationCreate,
) -> Notification:
    notification = Notification(
        workspace_id=workspace_id,
        user_id=notification_in.user_id,
        work_item_id=notification_in.work_item_id,
        title=notification_in.title,
        message=notification_in.message,
        notification_type=notification_in.notification_type,
        priority=notification_in.priority,
        delivery_channel=notification_in.delivery_channel,
        delivery_status=notification_in.delivery_status,
        retry_count=notification_in.retry_count,
        failure_reason=notification_in.failure_reason,
        is_read=notification_in.is_read,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def get_notification_by_id(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    notification_id: uuid.UUID,
) -> Notification | None:
    statement = select(Notification).where(
        Notification.id == notification_id,
        Notification.workspace_id == workspace_id,
        Notification.user_id == user_id,
    )
    return db.execute(statement).scalar_one_or_none()


def list_notifications(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    is_read: bool | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[Notification]:
    statement = select(Notification).where(
        Notification.workspace_id == workspace_id,
        Notification.user_id == user_id,
    )
    if is_read is not None:
        statement = statement.where(Notification.is_read.is_(is_read))
    statement = (
        statement
        .order_by(Notification.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(db.execute(statement).scalars().all())


def list_organization_scoped_for_user(
    db: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID | None = None,
    is_read: bool | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[Notification], int, int]:
    """Return the caller's ORGANIZATION-SCOPED notifications, newest first."""
    base = select(Notification).where(
        Notification.organization_id == organization_id,
        Notification.user_id == user_id,
        Notification.workspace_id.is_(None),
    )
    if is_read is not None:
        base = base.where(Notification.is_read.is_(is_read))

    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()

    unread = db.execute(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.organization_id == organization_id,
            Notification.user_id == user_id,
            Notification.workspace_id.is_(None),
            Notification.is_read.is_(False),
        )
    ).scalar_one()

    rows = list(
        db.execute(
            base.order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return rows, total, unread


def get_organization_scoped_for_user(
    db: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    notification_id: uuid.UUID,
) -> Notification | None:
    """One organization-scoped notification belonging to this caller."""
    statement = select(Notification).where(
        Notification.id == notification_id,
        Notification.organization_id == organization_id,
        Notification.user_id == user_id,
        Notification.workspace_id.is_(None),
    )
    return db.execute(statement).scalar_one_or_none()


def update_notification_read_status(
    db: Session,
    *,
    notification: Notification,
    is_read: bool,
) -> Notification:
    notification.is_read = is_read
    db.commit()
    db.refresh(notification)
    return notification


def update_notification_delivery_status(
    db: Session,
    *,
    notification: Notification,
    delivery_status: NotificationStatus,
    retry_count: int | None = None,
    failure_reason: str | None = None,
) -> Notification:
    notification.delivery_status = delivery_status
    if retry_count is not None:
        notification.retry_count = retry_count
    notification.failure_reason = failure_reason
    db.commit()
    db.refresh(notification)
    return notification


def mark_all_notifications_as_read(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> int:
    statement = (
        update(Notification)
        .where(
            Notification.workspace_id == workspace_id,
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        )
        .values(is_read=True)
    )
    result = db.execute(statement)
    db.commit()
    return result.rowcount or 0


def delete_notification(
    db: Session,
    *,
    notification: Notification,
) -> None:
    db.delete(notification)
    db.commit()