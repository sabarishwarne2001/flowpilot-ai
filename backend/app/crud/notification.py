"""
Database CRUD repository for FlowPilot AI notifications.

Responsible only for persistence operations.
Business rules remain inside the notification service.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.notification import NotificationStatus
from app.schemas.notification import NotificationCreate


# ============================================================================
# Create
# ============================================================================

def create_notification(
    db: Session,
    *,
    notification_in: NotificationCreate,
) -> Notification:
    """
    Persist a new notification.
    """

    notification = Notification(
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


# ============================================================================
# Read
# ============================================================================

def get_notification_by_id(
    db: Session,
    *,
    notification_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Notification | None:
    """
    Retrieve a notification belonging to a user.
    """

    statement = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == user_id,
    )

    return db.execute(statement).scalar_one_or_none()


def get_notifications_for_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    is_read: bool | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[Notification]:
    """
    Retrieve notifications for a user ordered newest-first.
    """

    statement = select(Notification).where(
        Notification.user_id == user_id
    )

    if is_read is not None:
        statement = statement.where(
            Notification.is_read.is_(is_read)
        )

    statement = (
        statement
        .order_by(Notification.created_at.desc())
        .offset(skip)
        .limit(limit)
    )

    return list(
        db.execute(statement).scalars().all()
    )


# ============================================================================
# Update
# ============================================================================

def update_notification_read_status(
    db: Session,
    *,
    notification: Notification,
    is_read: bool,
) -> Notification:
    """
    Update read/unread state.
    """

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
    """
    Update notification delivery metadata.
    """

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
    user_id: uuid.UUID,
) -> int:
    """
    Mark every unread notification as read.
    """

    statement = (
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        )
        .values(
            is_read=True,
        )
    )

    result = db.execute(statement)

    db.commit()

    return result.rowcount or 0


# ============================================================================
# Delete
# ============================================================================

def delete_notification(
    db: Session,
    *,
    notification: Notification,
) -> None:
    """
    Delete a notification.
    """

    db.delete(notification)
    db.commit()