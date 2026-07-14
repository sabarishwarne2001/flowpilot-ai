"""
CRUD operations for Email Settings.

Provides a single source of truth for persistent SMTP configuration
owned by each authenticated user.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.email_settings import EmailSettings
from app.schemas.email_settings import EmailSettingsCreate
from app.schemas.email_settings import EmailSettingsUpdate

from cryptography.fernet import Fernet

from app.core.config import settings as app_settings


# ============================================================================
# Encryption
# ============================================================================

_fernet = Fernet(
    app_settings.EMAIL_ENCRYPTION_KEY.get_secret_value().encode()
)


def encrypt_password(password: str) -> str:
    """
    Encrypts an SMTP password before persisting it.
    """

    return _fernet.encrypt(
        password.encode()
    ).decode()


# ============================================================================
# Create
# ============================================================================


def create_email_settings(
    db: Session,
    *,
    user_id: uuid.UUID,
    settings_in: EmailSettingsCreate,
) -> EmailSettings:
    """
    Creates SMTP configuration for a user.

    Each user may own only one configuration.
    """

    settings = EmailSettings(
        user_id=user_id,
        smtp_host=settings_in.smtp_host,
        smtp_port=settings_in.smtp_port,
        smtp_username=settings_in.smtp_username,
        encrypted_password=encrypt_password(
            settings_in.smtp_password,
        ),
        sender_name=settings_in.sender_name,
        encryption=settings_in.encryption,
        is_enabled=settings_in.is_enabled,
    )

    db.add(settings)
    db.commit()
    db.refresh(settings)

    return settings


# ============================================================================
# Read
# ============================================================================


def get_email_settings(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> EmailSettings | None:
    """
    Returns SMTP settings belonging to the user.
    """

    return db.execute(
        select(EmailSettings).where(
            EmailSettings.user_id == user_id,
        )
    ).scalar_one_or_none()


# ============================================================================
# Exists
# ============================================================================


def email_settings_exist(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> bool:
    """
    Returns True when SMTP settings already exist.
    """

    return (
        get_email_settings(
            db,
            user_id=user_id,
        )
        is not None
    )


# ============================================================================
# Update
# ============================================================================


def update_email_settings(
    db: Session,
    *,
    settings: EmailSettings,
    settings_in: EmailSettingsUpdate,
) -> EmailSettings:
    """
    Updates an existing SMTP configuration.
    """

    update_data = settings_in.model_dump(
        exclude_unset=True,
    )

    if "smtp_password" in update_data:

        update_data["encrypted_password"] = encrypt_password(
            update_data.pop("smtp_password")
        )

    for field, value in update_data.items():
        setattr(settings, field, value)

    db.add(settings)
    db.commit()
    db.refresh(settings)

    return settings


# ============================================================================
# Delete
# ============================================================================


def delete_email_settings(
    db: Session,
    *,
    settings: EmailSettings,
) -> None:
    """
    Deletes SMTP configuration.
    """

    db.delete(settings)
    db.commit()


# ============================================================================
# Upsert
# ============================================================================


def upsert_email_settings(
    db: Session,
    *,
    user_id: uuid.UUID,
    settings_in: EmailSettingsCreate,
) -> EmailSettings:
    """
    Creates SMTP settings if none exist,
    otherwise updates the existing record.
    """

    settings = get_email_settings(
        db,
        user_id=user_id,
    )

    if settings is None:
        return create_email_settings(
            db,
            user_id=user_id,
            settings_in=settings_in,
        )

    update = EmailSettingsUpdate(
        **settings_in.model_dump(),
    )

    return update_email_settings(
        db,
        settings=settings,
        settings_in=update,
    )