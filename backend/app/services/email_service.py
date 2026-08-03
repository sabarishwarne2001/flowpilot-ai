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

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings as app_settings
from app.core.smtp import SMTPConfig
from app.models.email_settings import EmailEncryption, EmailSettings

from cryptography.fernet import Fernet


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
    # Private Helpers & Adaptation
    # ======================================================================

    def _resolve_config(self, settings: EmailSettings | SMTPConfig) -> SMTPConfig:
        """
        Adapts legacy database model EmailSettings or SMTPConfig to a unified SMTPConfig.
        """
        if isinstance(settings, EmailSettings):
            return SMTPConfig(
                smtp_host=settings.smtp_host,
                smtp_port=settings.smtp_port,
                smtp_username=settings.smtp_username,
                smtp_password=self.decrypt_password(settings.encrypted_password),
                sender_name=settings.sender_name,
                encryption=settings.encryption,
            )
        return settings

    def _create_client(
        self,
        config: SMTPConfig,
    ) -> smtplib.SMTP:
        """
        Creates an authenticated SMTP client from an SMTPConfig.
        """
        if config.encryption == EmailEncryption.SSL:
            client = smtplib.SMTP_SSL(
                host=config.smtp_host,
                port=config.smtp_port,
                timeout=20,
            )
        else:
            client = smtplib.SMTP(
                host=config.smtp_host,
                port=config.smtp_port,
                timeout=20,
            )
            client.ehlo()

            if config.encryption == EmailEncryption.TLS:
                client.starttls()
                client.ehlo()

        client.login(
            config.smtp_username,
            config.smtp_password,
        )

        return client

    def _send_message(
        self,
        config: SMTPConfig,
        message: EmailMessage,
    ) -> tuple[bool, str]:
        """
        Executes client connection, authentication, message delivery, and cleanup.
        """
        client = None
        try:
            client = self._create_client(config)
            client.send_message(message)
            client.quit()
            return True, "Email sent successfully."
        except Exception as exc:
            if client:
                try:
                    client.quit()
                except Exception:
                    pass
            return False, str(exc)

    # ======================================================================
    # Test Connection
    # ======================================================================

    def test_connection(
        self,
        settings: EmailSettings,
    ) -> tuple[bool, str]:
        """
        Verifies SMTP credentials. Maintains complete backwards compatibility.
        """
        client = None
        try:
            config = self._resolve_config(settings)
            client = self._create_client(config)
            client.quit()
            return True, "SMTP connection successful."
        except Exception as exc:
            if client:
                try:
                    client.quit()
                except Exception:
                    pass
            return False, str(exc)

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
        Sends a plain-text email. Maintains complete backwards compatibility.
        """
        config = self._resolve_config(settings)
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{config.sender_name} <{config.smtp_username}>"
        message["To"] = recipient
        message.set_content(body)

        return self._send_message(config, message)

    def send_html_email(
        self,
        *,
        settings: EmailSettings | SMTPConfig,
        recipient: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> tuple[bool, str]:
        """
        Sends a multipart HTML/text alternative email.
        """
        config = self._resolve_config(settings)
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{config.sender_name} <{config.smtp_username}>"
        message["To"] = recipient

        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")

        return self._send_message(config, message)


email_service = EmailService()