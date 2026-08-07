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

    smtp_password = (
        app_settings.SMTP_PASSWORD.get_secret_value()
        if app_settings.SMTP_PASSWORD
        else ""
    )
    encryption = (
        EmailEncryption.TLS
        if app_settings.SMTP_USE_TLS
        else EmailEncryption.NONE
    )

    return SMTPConfig(
        smtp_host=app_settings.SMTP_HOST,
        smtp_port=app_settings.SMTP_PORT,
        smtp_username=app_settings.SMTP_USERNAME or app_settings.SMTP_FROM_EMAIL,
        smtp_password=smtp_password,
        sender_name=app_settings.PROJECT_NAME,
        encryption=encryption,
    )