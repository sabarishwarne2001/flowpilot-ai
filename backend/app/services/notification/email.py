"""
Email Notification Provider for FlowPilot AI.

Acts as a lightweight adapter between the notification framework and
EmailService. All SMTP communication is delegated to EmailService.

This keeps SMTP logic centralized in one production-grade service.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.services.email_service import email_service
from app.services.notification.base import NotificationProvider

if TYPE_CHECKING:
    from app.models.email_settings import EmailSettings
    from app.core.smtp import SMTPConfig

logger = logging.getLogger("app.services.notification.email")


class EmailNotificationProvider(NotificationProvider):
    """
    Email notification adapter.

    SMTP operations are delegated to EmailService.
    """

    async def send(
        self,
        *,
        settings: EmailSettings | SMTPConfig,
        recipient: str,
        title: str,
        body: str,
        html_body: str | None = None,
    ) -> bool:
        """
        Sends an email using the supplied SMTP settings.
        
        Supports both plain-text and multipart HTML email formats.
        """

        logger.info(
            "Sending email notification to '%s'.",
            recipient,
        )

        if html_body:
            success, message = await asyncio.to_thread(
                email_service.send_html_email,
                settings=settings,
                recipient=recipient,
                subject=title,
                html_body=html_body,
                text_body=body,
            )
        else:
            success, message = await asyncio.to_thread(
                email_service.send_email,
                settings=settings,
                recipient=recipient,
                subject=title,
                body=body,
            )

        if success:
            logger.info("Email notification delivered successfully.")
        else:
            logger.error(
                "Email notification failed: %s",
                message,
            )

        return success


email_notification_provider = EmailNotificationProvider()