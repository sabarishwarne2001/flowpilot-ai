"""
SMTP Connection Configuration Utilities for FlowPilot AI.
"""

from __future__ import annotations

import uuid
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.encryption import decrypt_password
from app.crud.email_settings import get_email_settings
from app.models.email_settings import EmailEncryption


class SMTPConfig(BaseModel):
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    sender_name: str
    encryption: EmailEncryption

    from_email: str | None = None

    @property
    def sender_address(self) -> str:
        return self.from_email or self.smtp_username


def resolve_smtp_config(
    db: Session,
    workspace_id: uuid.UUID | None,
    organization_id: uuid.UUID | None = None,
) -> SMTPConfig:
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

    if organization_id:
        from app.services.organization_email_settings_service import (
            resolve_organization_smtp_config,
        )

        organization_config = resolve_organization_smtp_config(
            db, organization_id=organization_id
        )
        if organization_config is not None:
            return organization_config

    from app.core.platform_email import platform_smtp_config

    return platform_smtp_config()