"""
SMTP Connection Configuration Utilities for FlowPilot AI.

Defines Pydantic configuration schemas and resolution engines to handle SMTP 
outbound parameters dynamically. Decoupled from relational database models.
"""

from __future__ import annotations

import uuid
from pydantic import BaseModel
from sqlalchemy.orm import Session
from cryptography.fernet import Fernet

from app.core.config import settings as app_settings
from app.crud.email_settings import get_email_settings, encrypt_password
from app.models.email_settings import EmailEncryption, EmailSettings

# Initialize the shared decryption cipher
_fernet = Fernet(app_settings.EMAIL_ENCRYPTION_KEY.get_secret_value().encode())


def decrypt_password(encrypted_password: str) -> str:
    """
    Decrypts a custom SMTP password using the project-wide Fernet key.
    """
    return _fernet.decrypt(encrypted_password.encode()).decode()


class SMTPConfig(BaseModel):
    """
    Lightweight, immutable configuration schema for SMTP credentials.
    Decoupled from ORM models to cleanly represent transient system settings.
    """
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    sender_name: str
    encryption: EmailEncryption


def resolve_smtp_config(
    db: Session,
    user_id: uuid.UUID | None,
) -> SMTPConfig:
    """
    Resolves active SMTP configuration by prioritizing database-backed custom user settings.
    Falls back gracefully to system default configurations without using transient database models.
    """
    if user_id:
        user_settings = get_email_settings(db, user_id=user_id)
        if user_settings and user_settings.is_enabled:
            password = decrypt_password(user_settings.encrypted_password)
            return SMTPConfig(
                smtp_host=user_settings.smtp_host,
                smtp_port=user_settings.smtp_port,
                smtp_username=user_settings.smtp_username,
                smtp_password=password,
                sender_name=user_settings.sender_name,
                encryption=user_settings.encryption,
            )

    # Resolve global fallback credentials from Pydantic config
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