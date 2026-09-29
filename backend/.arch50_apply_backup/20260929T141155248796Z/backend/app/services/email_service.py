"""
SMTP email delivery service for FlowPilot AI.
"""

from __future__ import annotations

import ipaddress
import logging
import smtplib
import socket
import ssl
from email.message import EmailMessage

from app.core.config import settings as app_settings
from app.core.encryption import decrypt_password
from app.core.smtp import SMTPConfig
from app.models.email_settings import EmailEncryption, EmailSettings


class SMTPHostRefused(ValueError):
    """A tenant-supplied SMTP host resolved to an address we will not reach."""


def _allowlisted(host: str, addresses: list[str]) -> bool:
    entries = [e.strip() for e in (app_settings.SMTP_PRIVATE_HOST_ALLOWLIST or []) if e.strip()]
    if not entries:
        return False
    lowered = host.strip().lower().rstrip(".")
    for entry in entries:
        if "/" in entry:
            try:
                network = ipaddress.ip_network(entry, strict=False)
            except ValueError:
                continue
            if addresses and all(ipaddress.ip_address(a) in network for a in addresses):
                return True
        elif lowered == entry.lower().rstrip("."):
            return True
    return False


def validate_smtp_host(host: str, port: int) -> str | None:
    """Return the address to connect to, or raise SMTPHostRefused.

    None means "connect by name" (development allowance or operator
    allowlist); a string is the validated, pinned IP.
    """
    from app.core.ssrf_client import SSRFClientError, resolve_and_validate

    if not host or not host.strip():
        raise SMTPHostRefused("The SMTP host is empty.")
    development = str(getattr(app_settings, "ENVIRONMENT", "")).lower() == "development"
    try:
        addresses = resolve_and_validate(host.strip(), int(port), timeout=5.0)
    except SSRFClientError as exc:
        # The name resolves to a private/loopback/metadata range (or not at
        # all). Allowed only for an operator-listed relay or in development.
        try:
            infos = socket.getaddrinfo(host.strip(), int(port), proto=socket.IPPROTO_TCP)
            resolved = sorted({info[4][0] for info in infos})
        except OSError:
            resolved = []
        if resolved and _allowlisted(host, resolved):
            return None
        if development and getattr(app_settings, "SMTP_ALLOW_PRIVATE_IN_DEVELOPMENT", True):
            return None
        raise SMTPHostRefused(
            f"SMTP host '{host}' is not reachable from FlowPilot: {exc} "
            "Use your provider's public SMTP hostname."
        ) from exc
    return addresses[0]


class _PinnedSMTP(smtplib.SMTP):
    """smtplib.SMTP that connects to a pre-validated IP.

    `self._host` stays the configured hostname, so STARTTLS verifies the
    certificate against the name the tenant typed, not the IP.
    """

    def __init__(self, *, pinned_ip: str | None, **kwargs: object) -> None:
        self._pinned_ip = pinned_ip
        super().__init__(**kwargs)  # type: ignore[arg-type]

    def _get_socket(self, host, port, timeout):  # type: ignore[no-untyped-def]
        target = self._pinned_ip or host
        return socket.create_connection((target, port), timeout, self.source_address)


class _PinnedSMTP_SSL(smtplib.SMTP_SSL):
    def __init__(self, *, pinned_ip: str | None, **kwargs: object) -> None:
        self._pinned_ip = pinned_ip
        super().__init__(**kwargs)  # type: ignore[arg-type]

    def _get_socket(self, host, port, timeout):  # type: ignore[no-untyped-def]
        target = self._pinned_ip or host
        raw = socket.create_connection((target, port), timeout, self.source_address)
        return self.context.wrap_socket(raw, server_hostname=self._host)



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
        # HARDENING-T1:D23. Two changes, both on the one path every SMTP send
        # and every "send test email" takes:
        #
        # 1. Tenant hosts are resolved and checked BEFORE connecting, and the
        #    socket is pinned to the checked address (a second lookup could
        #    rebind the name to 127.0.0.1). Before this, a tenant admin could
        #    point SMTP at redis:6379 or 169.254.169.254 and read the error
        #    text from the test button — an internal port scanner.
        # 2. TLS certificates are verified. smtplib's default context
        #    (`ssl._create_stdlib_context`) does not verify them, so SSL and
        #    STARTTLS were encrypted but unauthenticated.
        pinned_ip = None if config.trusted else validate_smtp_host(
            config.smtp_host, int(config.smtp_port)
        )
        context = ssl.create_default_context()
        if config.encryption == EmailEncryption.SSL:
            client: smtplib.SMTP = _PinnedSMTP_SSL(
                pinned_ip=pinned_ip,
                host=config.smtp_host,
                port=config.smtp_port,
                timeout=20,
                context=context,
            )
        else:
            client = _PinnedSMTP(
                pinned_ip=pinned_ip,
                host=config.smtp_host,
                port=config.smtp_port,
                timeout=20,
            )
            client.ehlo()
            if config.encryption == EmailEncryption.TLS:
                client.starttls(context=context)
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
