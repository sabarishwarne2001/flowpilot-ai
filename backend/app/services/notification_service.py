"""
Business orchestration service for FlowPilot AI notifications.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app import crud
from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
    NotificationType,
)
from app.models.user import User
from app.models.work_item import WorkItem
from app.schemas.notification import NotificationCreate
from app.services.notification.dispatcher import notification_dispatcher

if TYPE_CHECKING:
    from app.models.email_settings import EmailSettings
    from app.core.smtp import SMTPConfig

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self) -> None:
        self.dispatcher = notification_dispatcher

    async def send_notification(
        self,
        *,
        db: Session,
        workspace_id: uuid.UUID,
        user: User,
        title: str,
        message: str,
        notification_type: NotificationType,
        priority: NotificationPriority = NotificationPriority.INFO,
        delivery_channel: NotificationChannel = NotificationChannel.IN_APP,
        work_item: WorkItem | None = None,
        settings: EmailSettings | SMTPConfig | None = None,
        html_body: str | None = None,
    ) -> Notification:

        logger.info(
            "Creating notification for user %s inside workspace %s.",
            user.id,
            workspace_id,
        )

        notification_in = NotificationCreate(
            user_id=user.id,
            work_item_id=(
                work_item.id
                if work_item is not None
                else None
            ),
            title=title,
            message=message,
            notification_type=notification_type,
            priority=priority,
            delivery_channel=delivery_channel,
        )

        notification = crud.create_notification(
            db,
            workspace_id=workspace_id,
            notification_in=notification_in,
        )

        if delivery_channel == NotificationChannel.IN_APP:
            notification = crud.update_notification_delivery_status(
                db,
                notification=notification,
                delivery_status=NotificationStatus.SENT,
            )
            logger.info(
                "Notification %s delivered via in-app channel.",
                notification.id,
            )
            return notification

        if delivery_channel == NotificationChannel.EMAIL:
            try:
                if settings is None:
                    from app.core.smtp import resolve_smtp_config
                    settings = resolve_smtp_config(db, workspace_id=workspace_id)

                success = await self.dispatcher.send(
                    action_type="email",
                    settings=settings,
                    recipient=user.email,
                    title=title,
                    body=message,
                    html_body=html_body,
                )

                if success:
                    notification = crud.update_notification_delivery_status(
                        db,
                        notification=notification,
                        delivery_status=NotificationStatus.SENT,
                    )
                    logger.info("Email notification %s delivered.", notification.id)
                else:
                    notification = crud.update_notification_delivery_status(
                        db,
                        notification=notification,
                        delivery_status=NotificationStatus.FAILED,
                        retry_count=notification.retry_count + 1,
                        failure_reason="Email provider reported delivery failure.",
                    )
                    logger.warning("Email notification %s failed.", notification.id)

            except Exception as exc:
                notification = crud.update_notification_delivery_status(
                    db,
                    notification=notification,
                    delivery_status=NotificationStatus.FAILED,
                    retry_count=notification.retry_count + 1,
                    failure_reason=str(exc),
                )
                logger.exception("Unexpected email notification failure.")

            return notification

        return notification


notification_service = NotificationService()
