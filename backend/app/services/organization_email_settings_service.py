"""
Per-organization SMTP configuration service.

ARCH-06 Step 8 / ARCH-07 Step 3 / ARCH-07 Step 8: Uses central app.core.encryption.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.encryption import decrypt_password, encrypt_password
from app.core.smtp import SMTPConfig
from app.models.organization_email_settings import OrganizationEmailSettings
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.user import User
from app.schemas.organization_email_settings import (
    OrganizationEmailSettingsUpdate,
)
from app.services import audit_service

logger = logging.getLogger("app.services.organization_email_settings")


class OrganizationEmailSettingsError(Exception):
    """Base class for organization SMTP configuration failures."""


class IncompleteConfigurationError(OrganizationEmailSettingsError):
    """Enabling was requested but required fields are missing."""


def get_settings(
    db: Session, *, organization_id: uuid.UUID
) -> OrganizationEmailSettings | None:
    return db.execute(
        select(OrganizationEmailSettings).where(
            OrganizationEmailSettings.organization_id == organization_id
        )
    ).scalar_one_or_none()


def get_or_create_settings(
    db: Session, *, organization_id: uuid.UUID
) -> OrganizationEmailSettings:
    existing = get_settings(db, organization_id=organization_id)
    if existing is not None:
        return existing

    created = OrganizationEmailSettings(
        organization_id=organization_id,
        is_enabled=False,
    )
    db.add(created)
    db.commit()
    db.refresh(created)
    return created


def set_settings(
    db: Session,
    *,
    organization_id: uuid.UUID,
    payload: OrganizationEmailSettingsUpdate,
    actor: User,
    request: Any = None,
) -> OrganizationEmailSettings:
    row = get_or_create_settings(db, organization_id=organization_id)

    savepoint = db.begin_nested()

    supplied = payload.model_dump(exclude_unset=True)
    changed_fields: list[str] = []

    if "smtp_password" in supplied:
        plaintext = supplied.pop("smtp_password")
        if plaintext is not None:
            row.encrypted_password = encrypt_password(plaintext)
            changed_fields.append("smtp_password")

    for field, value in supplied.items():
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed_fields.append(field)

    if row.is_enabled and not row.is_complete:
        savepoint.rollback()
        raise IncompleteConfigurationError(
            "SMTP host, port, username, password, sender name, and "
            "encryption are all required before this configuration can be "
            "enabled."
        )

    savepoint.commit()

    row.updated_by_user_id = actor.id
    db.add(row)

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor.id,
        resource_type=AuditResourceType.EMAIL_SETTINGS,
        resource_id=row.id,
        action=AuditAction.UPDATED,
        details={
            **audit_service.actor_snapshot(actor),
            "changed_fields": sorted(changed_fields),
            "smtp_host": row.smtp_host,
            "smtp_port": row.smtp_port,
            "is_enabled": row.is_enabled,
        },
        **audit_service.context_from_request(request),
    )

    db.commit()
    db.refresh(row)
    return row


def to_smtp_config(row: OrganizationEmailSettings) -> SMTPConfig:
    return SMTPConfig(
        smtp_host=row.smtp_host,
        smtp_port=row.smtp_port,
        smtp_username=row.smtp_username,
        smtp_password=decrypt_password(row.encrypted_password),
        sender_name=row.sender_name,
        encryption=row.encryption,
        from_email=row.sender_email,
    )


def resolve_organization_smtp_config(
    db: Session, *, organization_id: uuid.UUID | None
) -> SMTPConfig | None:
    if organization_id is None:
        return None

    row = get_settings(db, organization_id=organization_id)
    if row is None or not row.is_enabled:
        return None

    if not row.is_complete:
        logger.error(
            "ORG_SMTP_ENABLED_BUT_INCOMPLETE | organization=%s | "
            "falling back to platform default",
            organization_id,
        )
        return None

    return to_smtp_config(row)