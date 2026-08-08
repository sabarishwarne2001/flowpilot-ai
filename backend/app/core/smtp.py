"""
SMTP Connection Configuration Utilities for FlowPilot AI.
"""

from __future__ import annotations

import uuid
from pydantic import BaseModel
from sqlalchemy.orm import Session
from cryptography.fernet import Fernet

from app.core.config import settings as app_settings
from app.crud.email_settings import get_email_settings
from app.models.email_settings import EmailEncryption

_fernet = Fernet(app_settings.EMAIL_ENCRYPTION_KEY.get_secret_value().encode())


def decrypt_password(encrypted_password: str) -> str:
    return _fernet.decrypt(encrypted_password.encode()).decode()


class SMTPConfig(BaseModel):
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    sender_name: str
    encryption: EmailEncryption

    # Optional visible sender. Hosted relays authenticate as an identity that
    # is not an address at all — SendGrid as "apikey", Mailgun as
    # "postmaster@mg.example.com" — so deriving the From header from the login
    # username yields an unroutable address and a hard bounce. Workspace SMTP
    # rows leave this None and keep the previous behaviour exactly.
    from_email: str | None = None

    @property
    def sender_address(self) -> str:
        """The address that appears in the From header."""
        return self.from_email or self.smtp_username


def resolve_smtp_config(
    db: Session,
    workspace_id: uuid.UUID | None,
) -> SMTPConfig:
    """
    Resolves active SMTP configuration strictly by workspace_id.
    """
    if workspace_id:
        workspace_settings = get_email_settings(db, workspace_id=workspace_id)
        if workspace_settings and workspace_settings.is_enabled:
            password = decrypt_password(workspace_settings.encrypted_password)
            return SMTPConfig(
                smtp_host=workspace_settings.smtp_host,
                smtp_port=workspace_settings.smtp_port,
                smtp_username=workspace_settings.smtp_username,
                smtp_password=password,
                sender_name=workspace_settings.sender_name,
                encryption=workspace_settings.encryption,
            )

    # No workspace, or a workspace with no enabled SMTP row: this is FlowPilot
    # speaking for itself. Delegated so the platform credentials have exactly
    # one definition and the two paths cannot drift. ARCH-03 §B.1.
    #
    # Imported at call time: platform_email imports SMTPConfig from this
    # module, so a module-level import here would be circular.
    from app.core.platform_email import platform_smtp_config

    return platform_smtp_config()