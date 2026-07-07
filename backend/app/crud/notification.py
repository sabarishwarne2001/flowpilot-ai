"""
Database CRUD (Create, Read, Update, Delete) repository layer for in-app
Notifications.

Provides database access for user notifications while enforcing user ownership
and keeping business logic outside the repository layer.
"""

import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.notification import Notification


def create_notification(
    db: Session,
    *,
    user_id: uuid.UUID,
    title: str,
    message: str,
    work_item_id: uuid.UUID | None = None,
) -> Notification:
    """
    Create and persist a new notification.
    """
    db_obj = Notification(
        user_id=user_id,
        title=title,
        message=message,
        work_item_id=work_item_id,
        is_read=False,
    )

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)

    return db_obj


def get_notification_by_id(
    db: Session,
    *,
    notification_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Notification | None:
    """
    Retrieve a notification owned by a user.
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
    Retrieve paginated notifications for a user.

    Optionally filter by read/unread status.
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

    return list(db.execute(statement).scalars().all())


def update_notification_read_status(
    db: Session,
    *,
    db_obj: Notification,
    is_read: bool,
) -> Notification:
    """
    Update the read/unread status of a notification.
    """
    db_obj.is_read = is_read

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)

    return db_obj


def mark_all_notifications_as_read(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> int:
    """
    Mark all unread notifications for a user as read.

    Returns the number of updated rows.
    """
    statement = (
        update(Notification)
        .where(
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
    db_obj: Notification,
) -> bool:
    """
    Delete a notification.
    """
    db.delete(db_obj)
    db.commit()

    return True