"""
SMTP-based Email Notification Provider for FlowPilot AI.

Implements the NotificationProvider interface using Python's standard SMTP
library. Blocking SMTP operations are executed in a background worker thread
to avoid blocking the FastAPI event loop.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import settings
from app.services.notification.base import NotificationProvider

logger = logging.getLogger("app.services.notification.email")


class EmailNotificationProvider(NotificationProvider):
    """
    SMTP implementation of the NotificationProvider interface.
    """

    def _send_email_sync(
        self,
        recipient: str,
        title: str,
        body: str,
    ) -> bool:
        """
        Perform the blocking SMTP transaction.

        Returns:
            True if the email was delivered successfully.
            False otherwise.
        """

        logger.info(
            "Sending email notification to '%s' via %s:%s",
            recipient,
            settings.SMTP_HOST,
            settings.SMTP_PORT,
        )

        message = EmailMessage()
        message["Subject"] = title
        message["From"] = settings.SMTP_FROM_EMAIL
        message["To"] = recipient

        message.set_content(body)

        message.add_alternative(
            f"""
            <html>
                <body>
                    <pre style="font-family:Arial,sans-serif;">
        {body}
                    </pre>
                </body>
            </html>
            """,
            subtype="html",
        )

        password = (
            settings.SMTP_PASSWORD.get_secret_value()
            if settings.SMTP_PASSWORD
            else ""
        )

        try:
            with smtplib.SMTP(
                host=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                timeout=settings.SMTP_TIMEOUT,
            ) as server:

                server.ehlo()

                if settings.SMTP_USE_TLS:
                    logger.info("Starting TLS session.")
                    context = ssl.create_default_context()
                    server.starttls(context=context)
                    server.ehlo()

                if settings.SMTP_USERNAME:
                    logger.info("Authenticating SMTP user '%s'.", settings.SMTP_USERNAME)
                    server.login(
                        settings.SMTP_USERNAME,
                        password,
                    )

                server.send_message(message)

            logger.info("Email successfully delivered to '%s'.", recipient)
            logger.info("SMTP transaction completed successfully.")
            return True

        except smtplib.SMTPException as exc:
            logger.exception("SMTP protocol error while sending email: %s", exc)

        except OSError as exc:
            logger.exception("Network error while sending email: %s", exc)

        except Exception:
            logger.exception("Unexpected error while sending email.")

        return False

    async def send(
        self,
        recipient: str,
        title: str,
        body: str,
    ) -> bool:
        """
        Send an email asynchronously by delegating SMTP work to a thread.
        """
        return await asyncio.to_thread(
            self._send_email_sync,
            recipient,
            title,
            body,
        )


email_notification_provider = EmailNotificationProvider()