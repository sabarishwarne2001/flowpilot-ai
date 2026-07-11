"""
Business orchestration service for FlowPilot AI notifications.

Coordinates notification creation, delivery, persistence, and status
tracking while delegating transport-specific work to notification
providers through the dispatcher.
"""

from __future__ import annotations

import logging

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


logger = logging.getLogger(__name__)


class NotificationService:
    """
    Central orchestration service for notifications.

    This service is responsible for:

    • Creating notification records
    • Dispatching notifications
    • Updating delivery status
    • Recording failures
    """

    def __init__(self) -> None:
        self.dispatcher = notification_dispatcher

    async def send_notification(
        self,
        *,
        db: Session,
        user: User,
        title: str,
        message: str,
        notification_type: NotificationType,
        priority: NotificationPriority = NotificationPriority.INFO,
        delivery_channel: NotificationChannel = NotificationChannel.IN_APP,
        work_item: WorkItem | None = None,
    ) -> Notification:
        """
        Create and optionally deliver a notification.

        Every notification is first persisted in the database.

        Depending on the requested delivery channel, the notification
        may then be dispatched through Email, Slack, Teams, Webhooks,
        or other providers.

        Returns
        -------
        Notification
            The persisted notification.
        """

        logger.info(
            "Creating notification for user %s.",
            user.id,
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
            notification_in=notification_in,
        )

        #
        # In-app notifications are considered delivered
        # immediately after being persisted.
        #
        if (
            delivery_channel
            == NotificationChannel.IN_APP
        ):

            notification = (
                crud.update_notification_delivery_status(
                    db,
                    notification=notification,
                    delivery_status=NotificationStatus.SENT,
                )
            )

            logger.info(
                "Notification %s delivered via in-app channel.",
                notification.id,
            )

            return notification

        #
        # Email delivery
        #
        if (
            delivery_channel
            == NotificationChannel.EMAIL
        ):

            try:

                success = await self.dispatcher.send(
                    action_type="email",
                    recipient=user.email,
                    title=title,
                    body=message,
                )

                if success:

                    notification = (
                        crud.update_notification_delivery_status(
                            db,
                            notification=notification,
                            delivery_status=NotificationStatus.SENT,
                        )
                    )

                    logger.info(
                        "Email notification %s delivered.",
                        notification.id,
                    )

                else:

                    notification = (
                        crud.update_notification_delivery_status(
                            db,
                            notification=notification,
                            delivery_status=NotificationStatus.FAILED,
                            retry_count=notification.retry_count + 1,
                            failure_reason=(
                                "Email provider reported delivery failure."
                            ),
                        )
                    )

                    logger.warning(
                        "Email notification %s failed.",
                        notification.id,
                    )

            except Exception as exc:

                notification = (
                    crud.update_notification_delivery_status(
                        db,
                        notification=notification,
                        delivery_status=NotificationStatus.FAILED,
                        retry_count=notification.retry_count + 1,
                        failure_reason=str(exc),
                    )
                )

                logger.exception(
                    "Unexpected email notification failure."
                )

            return notification

        #
        # Future delivery channels (Slack, Teams, SMS, Webhooks...)
        #
        return notification
    
notification_service = NotificationService()
