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
    from app.models.workspace_invitation import WorkspaceInvitation
    from app.core.smtp import SMTPConfig

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Central orchestration service for notifications.
    """

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

    async def send_workspace_invitation(
        self,
        db: Session,
        *,
        invitation: WorkspaceInvitation,
        workspace_name: str,
        plaintext_token: str,
    ) -> bool:
        """
        Sends the invitation email.

        plaintext_token is passed in rather than read from the invitation,
        because as of ARCH-03 CONTRACT there is nothing to read: the model
        holds only token_hash. The caller obtained it from IssuedInvitation
        and this is its last use.

        The link must carry the plaintext, never the hash. Putting the stored
        value in the link would make the database column itself the bearer
        credential and hand workspace membership to anyone with read access —
        which is the exact exposure this phase was opened to close.
        """
        from app.core.smtp import resolve_smtp_config
        from app.templates.emails.workspace_invitation import render_workspace_invitation
        from app.core.config import settings as app_settings

        # 1. Resolve outbound SMTP credentials using the workspace ID
        smtp_config = resolve_smtp_config(db, workspace_id=invitation.workspace_id)

        # 2. Build accept links
        # FRONTEND_URL is a declared setting as of ARCH-03 Step 1. The previous
        # getattr against a nonexistent FRONTEND_HOST always took the default,
        # so every invitation ever sent pointed at localhost:3000.
        #
        # The token still travels as a query parameter here. That violates
        # §B.9 and is fixed in Step 8, when the frontend accept route is moved
        # to fragment delivery; changing the link shape before the route can
        # read a fragment would break the one live pending invitation.
        accept_link = (
            f"{app_settings.FRONTEND_URL}/invitations/accept"
            f"?token={plaintext_token}"
        )
        
        role_display = invitation.role.value if hasattr(invitation.role, "value") else str(invitation.role)
        expiry_str = invitation.expires_at.strftime("%Y-%m-%d %H:%M:%S UTC")

        # 3. Render HTML templates
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
            await self.send_notification(
                db=db,
                workspace_id=invitation.workspace_id,
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
            return await self.dispatcher.send(
                action_type="email",
                settings=smtp_config,
                recipient=invitation.email,
                title=subject,
                body=text_body,
                html_body=html_body,
            )


notification_service = NotificationService()
