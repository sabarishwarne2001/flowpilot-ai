"""
SMTP email delivery service for FlowPilot AI.

Encapsulates all SMTP communication behind a reusable service layer.

Supports:

- Connection testing
- Sending emails
- Future HTML templates
- Future attachments
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.models.email_settings import EmailEncryption
from app.models.email_settings import EmailSettings

import logging

from cryptography.fernet import Fernet

from app.core.config import settings as app_settings


class EmailService:

    """
    Handles SMTP operations.
    """

    logger = logging.getLogger(__name__)

    def __init__(self) -> None:
        self._fernet = Fernet(
            app_settings.EMAIL_ENCRYPTION_KEY.get_secret_value().encode()
        )

    def decrypt_password(
        self,
        encrypted_password: str,
    ) -> str:
        """
        Returns decrypted SMTP password.
        """

        return self._fernet.decrypt(
            encrypted_password.encode()
        ).decode()


    # ======================================================================
    # Build SMTP Client
    # ======================================================================

    def _create_client(
        self,
        settings: EmailSettings,
    ) -> smtplib.SMTP:
        """
        Creates an authenticated SMTP client.
        """

        if settings.encryption == EmailEncryption.SSL:

            client = smtplib.SMTP_SSL(
                host=settings.smtp_host,
                port=settings.smtp_port,
                timeout=20,
            )

        else:

            client = smtplib.SMTP(
                host=settings.smtp_host,
                port=settings.smtp_port,
                timeout=20,
            )

            client.ehlo()

            if settings.encryption == EmailEncryption.TLS:
                client.starttls()
                client.ehlo()

        password = self.decrypt_password(
            settings.encrypted_password,
        )

        client.login(
            settings.smtp_username,
            password,
        )

        return client

    # ======================================================================
    # Test Connection
    # ======================================================================

    def test_connection(
        self,
        settings: EmailSettings,
    ) -> tuple[bool, str]:
        """
        Verifies SMTP credentials.
        """

        try:

            client = self._create_client(settings)

            client.quit()

            return (
                True,
                "SMTP connection successful.",
            )

        except Exception as exc:

            try:
                client.quit()
            except Exception:
                pass

            return (
                False,
                str(exc),
            )

    # ======================================================================
    # Send Email
    # ======================================================================

    def send_email(
        self,
        *,
        settings: EmailSettings,
        recipient: str,
        subject: str,
        body: str,
    ) -> tuple[bool, str]:
        """
        Sends a plain-text email.
        """

        try:

            message = EmailMessage()

            message["Subject"] = subject

            message["From"] = (
                f"{settings.sender_name} "
                f"<{settings.smtp_username}>"
            )

            message["To"] = recipient

            message.set_content(body)

            client = self._create_client(settings)

            client.send_message(message)

            client.quit()

            return (
                True,
                "Email sent successfully.",
            )

        except Exception as exc:

            try:
                client.quit()
            except Exception:
                pass

            return (
                False,
                str(exc),
            )


email_service = EmailService()