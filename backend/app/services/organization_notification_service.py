"""
Organization-scoped in-app notification emission.

ARCH-06 Step 9. Writes org-level notifications using `notifications.organization_id`.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
    NotificationType,
)

logger = logging.getLogger("app.services.organization_notifications")


def _emit(
    db: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    title: str,
    message: str,
    notification_type: NotificationType,
    priority: NotificationPriority,
) -> Notification:
    notification = Notification(
        organization_id=organization_id,
        workspace_id=None,
        user_id=user_id,
        title=title[:150],
        message=message,
        notification_type=notification_type,
        priority=priority,
        delivery_channel=NotificationChannel.IN_APP,
        delivery_status=NotificationStatus.SENT,
        retry_count=0,
        is_read=False,
    )
    db.add(notification)
    db.flush()
    return notification


def notify_ownership_transfer_proposed(
    db: Session,
    *,
    organization_id: uuid.UUID,
    recipient_user_id: uuid.UUID,
    organization_name: str,
    initiator_display: str,
) -> Notification:
    notification = _emit(
        db,
        organization_id=organization_id,
        user_id=recipient_user_id,
        title=f"Ownership transfer proposed for {organization_name}",
        message=(
            f"{initiator_display} has proposed transferring ownership of "
            f"{organization_name} to you. Review and accept or decline it "
            f"before the request expires."
        ),
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.WARNING,
    )
    logger.info(
        "ORG_NOTIFICATION | TRANSFER_PROPOSED | organization=%s | recipient=%s "
        "| notification=%s",
        organization_id,
        recipient_user_id,
        notification.id,
    )
    return notification


def notify_role_changed(
    db: Session,
    *,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID,
    organization_name: str,
    previous_role: str,
    new_role: str,
    actor_display: str,
) -> Notification:
    notification = _emit(
        db,
        organization_id=organization_id,
        user_id=target_user_id,
        title=f"Your role in {organization_name} changed",
        message=(
            f"{actor_display} changed your role in {organization_name} from "
            f"{previous_role} to {new_role}. If you were not expecting this, "
            f"contact an administrator of that organization."
        ),
        notification_type=NotificationType.SECURITY,
        priority=NotificationPriority.WARNING,
    )
    logger.info(
        "ORG_NOTIFICATION | ROLE_CHANGED | organization=%s | target=%s | "
        "%s -> %s | notification=%s",
        organization_id,
        target_user_id,
        previous_role,
        new_role,
        notification.id,
    )
    return notification