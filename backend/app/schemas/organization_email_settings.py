"""
Pydantic contracts for per-organization SMTP configuration.

ARCH-06 Step 8, §B.5 Option B.

THE PASSWORD IS ABSENT FROM THE RESPONSE, NOT MASKED
--------------------------------------------------------
`OrganizationEmailSettingsResponse` declares no password field of any kind.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.email_settings import EmailEncryption


# ============================================================================
# Write
# ============================================================================

class OrganizationEmailSettingsUpdate(BaseModel):
    """
    Partial update of an organization's SMTP configuration.
    """

    smtp_host: str | None = Field(default=None, min_length=1, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, min_length=1, max_length=255)
    smtp_password: str | None = Field(default=None, min_length=1, max_length=255)
    sender_name: str | None = Field(default=None, min_length=1, max_length=255)
    sender_email: EmailStr | None = None
    encryption: EmailEncryption | None = None
    is_enabled: bool | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("smtp_password")
    @classmethod
    def _reject_explicit_null_password(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError(
                "smtp_password cannot be blank. Omit the field to keep the "
                "existing password, or set is_enabled to false to stop using "
                "this configuration."
            )
        return stripped

    @field_validator("smtp_host", "smtp_username", "sender_name")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


# ============================================================================
# Read
# ============================================================================

class OrganizationEmailSettingsResponse(BaseModel):
    """
    An organization's SMTP configuration, minus the secret.
    """

    id: UUID
    organization_id: UUID

    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    sender_name: str | None = None
    sender_email: str | None = None
    encryption: EmailEncryption | None = None
    is_enabled: bool

    has_password: bool = Field(
        ...,
        description=(
            "Whether a password is stored. The only fact about the secret "
            "this API exposes — enough for a form to offer 'replace' instead "
            "of 'set', and nothing more."
        ),
    )
    is_complete: bool = Field(
        ...,
        description=(
            "Whether every field required to open an SMTP session is "
            "present. A row can be enabled only when this is true."
        ),
    )

    updated_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Test connection
# ============================================================================

class OrganizationEmailTestRequest(BaseModel):
    recipient: EmailStr


class OrganizationEmailTestResponse(BaseModel):
    success: bool
    message: str