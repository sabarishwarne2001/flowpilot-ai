"""
SMTP email delivery service for FlowPilot AI.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings as app_settings
from app.core.encryption import decrypt_password
from app.core.smtp import SMTPConfig
from app.models.email_settings import EmailEncryption, EmailSettings


class EmailService:
    logger = logging.getLogger(__name__)

    def _resolve_config(self, settings: EmailSettings | SMTPConfig) -> SMTPConfig:
        if isinstance(settings, EmailSettings):
            return SMTPConfig(
                smtp_host=settings.smtp_host,
                smtp_port=settings.smtp_port,
                smtp_username=settings.smtp_username,
                smtp_password=decrypt_password(settings.encrypted_password),
                sender_name=settings.sender_name,
                encryption=settings.encryption,
            )
        return settings

    def _create_client(
        self,
        config: SMTPConfig,
    ) -> smtplib.SMTP:
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

    def test_connection(
        self,
        settings: EmailSettings | SMTPConfig,
    ) -> tuple[bool, str]:
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

    def send_email(
        self,
        *,
        settings: EmailSettings | SMTPConfig,
        recipient: str,
        subject: str,
        body: str,
    ) -> tuple[bool, str]:
        config = self._resolve_config(settings)
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{config.sender_name} <{config.sender_address}>"
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
        reply_to: str | None = None,
    ) -> tuple[bool, str]:
        config = self._resolve_config(settings)
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{config.sender_name} <{config.sender_address}>"
        message["To"] = recipient

        if reply_to:
            message["Reply-To"] = reply_to

        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")

        return self._send_message(config, message)


email_service = EmailService()