"""
Business orchestration service for FlowPilot AI notifications.

Coordinates notification creation, delivery, persistence, and status
tracking while delegating transport-specific work to notification
providers through the dispatcher.
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
    from app.models.workspace_invitation import WorkspaceInvitation
    from app.core.smtp import SMTPConfig

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
        settings: EmailSettings | SMTPConfig | None = None,
        html_body: str | None = None,
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
                # Fallback: resolve active SMTPConfig dynamically if not supplied
                if settings is None:
                    from app.core.smtp import resolve_smtp_config
                    settings = resolve_smtp_config(db, user_id=user.id)

                success = await self.dispatcher.send(
                    action_type="email",
                    settings=settings,
                    recipient=user.email,
                    title=title,
                    body=message,
                    html_body=html_body,
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

    # ======================================================================
    # Workspace Invitation Orchestrator (Extended)
    # ======================================================================

    async def send_workspace_invitation(
        self,
        db: Session,
        *,
        invitation: WorkspaceInvitation,
        workspace_name: str,
    ) -> bool:
        """
        Orchestrates delivering a workspace invitation.

        Renders HTML/Text layouts, determines if the recipient possesses an 
        active User account, persists a database Notification record (for registered users), 
        and dispatches the email.
        """
        from app.core.smtp import resolve_smtp_config
        from app.templates.emails.workspace_invitation import render_workspace_invitation
        from app.core.config import settings as app_settings

        # 1. Resolve outbound SMTP credentials using the sender user profile
        smtp_config = resolve_smtp_config(db, user_id=invitation.inviter_id)

        # 2. Build accept links
        frontend_host = getattr(app_settings, "FRONTEND_HOST", "http://localhost:3000")
        accept_link = f"{frontend_host}/invitations/accept?token={invitation.token}"
        
        role_display = invitation.role.value if hasattr(invitation.role, "value") else str(invitation.role)
        expiry_str = invitation.expires_at.strftime("%Y-%m-%d %H:%M:%S UTC")

        # 3. Render HTML templates from dedicated templates package
        subject, html_body, text_body = render_workspace_invitation(
            workspace_name=workspace_name,
            role_display=role_display,
            accept_link=accept_link,
            expiry_str=expiry_str,
            brand_name=app_settings.PROJECT_NAME,
        )

        # 4. Check if the recipient already owns a registered account
        from app.crud import user as user_crud
        recipient_user = user_crud.get_user_by_email(db, email=invitation.email)

        if recipient_user:
            # If the user exists, persist a formal database Notification and dispatch
            await self.send_notification(
                db=db,
                user=recipient_user,
                title=subject,
                message=text_body,
                notification_type=NotificationType.EMAIL,
                priority=NotificationPriority.INFO,
                delivery_channel=NotificationChannel.EMAIL,
                settings=smtp_config,
                html_body=html_body,
            )
            return True
        else:
            # If the user is external, dispatch directly via dispatcher without database row
            return await self.dispatcher.send(
                action_type="email",
                settings=smtp_config,
                recipient=invitation.email,
                title=subject,
                body=text_body,
                html_body=html_body,
            )


notification_service = NotificationService()