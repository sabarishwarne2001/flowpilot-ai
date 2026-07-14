"""
Email Notification Provider for FlowPilot AI.

Acts as a lightweight adapter between the notification framework and
EmailService. All SMTP communication is delegated to EmailService.

This keeps SMTP logic centralized in one production-grade service.
"""

from __future__ import annotations

import asyncio
import logging

from app.models.email_settings import EmailSettings
from app.services.email_service import email_service
from app.services.notification.base import NotificationProvider

logger = logging.getLogger("app.services.notification.email")


class EmailNotificationProvider(NotificationProvider):
    """
    Email notification adapter.

    SMTP operations are delegated to EmailService.
    """

    async def send(
        self,
        *,
        settings: EmailSettings,
        recipient: str,
        title: str,
        body: str,
    ) -> bool:
        """
        Sends an email using the supplied SMTP settings.
        """

        logger.info(
            "Sending email notification to '%s'.",
            recipient,
        )

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