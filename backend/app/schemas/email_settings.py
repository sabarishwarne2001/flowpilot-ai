"""
Pydantic request and response schemas for Email Settings.

Defines the API contracts used to manage user SMTP configuration,
including validation rules and serialization behavior.
"""

from enum import Enum
from typing import Union

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import EmailStr
from pydantic import Field

from uuid import UUID
from datetime import datetime


# ============================================================================
# Email Encryption
# ============================================================================


class EmailEncryption(str, Enum):
    """
    Supported SMTP transport encryption methods.
    """

    NONE = "NONE"
    TLS = "TLS"
    SSL = "SSL"


# ============================================================================
# Base
# ============================================================================


class EmailSettingsBase(BaseModel):
    """
    Shared SMTP configuration fields.
    """

    smtp_host: str = Field(
        ...,
        min_length=1,
        max_length=255,
    )

    smtp_port: int = Field(
        ...,
        ge=1,
        le=65535,
    )

    smtp_username: EmailStr

    sender_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
    )

    encryption: EmailEncryption

    is_enabled: bool = True


# ============================================================================
# Create
# ============================================================================


class EmailSettingsCreate(EmailSettingsBase):
    """
    Initial SMTP configuration.
    """

    smtp_password: str = Field(
        ...,
        min_length=1,
        max_length=255,
    )


# ============================================================================
# Update
# ============================================================================


class EmailSettingsUpdate(BaseModel):
    """
    Partial update of SMTP configuration.
    """

    smtp_host: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    smtp_port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
    )

    smtp_username: EmailStr | None = None

    smtp_password: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    sender_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )

    encryption: EmailEncryption | None = None

    is_enabled: bool | None = None


# ============================================================================
# Response
# ============================================================================


class EmailSettingsResponse(EmailSettingsBase):
    """
    Serialized SMTP configuration returned to the frontend.
    """

    id: UUID
    workspace_id: UUID
    updated_by_user_id: Union[UUID, None] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


# ============================================================================
# Test Email
# ============================================================================


class TestEmailRequest(BaseModel):
    """
    Request body used when testing SMTP connectivity.
    """

    recipient: EmailStr


class TestEmailResponse(BaseModel):
    """
    SMTP test result.
    """

    success: bool

    message: str

    # ARCH40-S1:test-email-provenance. A test that says "sent" without saying
    # which relay carried it and as whom answers half the question.
    transport_layer: str | None = None
    from_address: str | None = None


# ============================================================================
# ARCH40-S1:email-override-schemas
# ============================================================================


class WorkspaceEmailOverrideUpdate(BaseModel):
    """A write to `workspace_email_overrides`.

    Every SMTP field is optional so a half-finished configuration can be
    saved — the state `email_settings` could not represent, having NOT NULL on
    six columns, which is why an administrator interrupted halfway lost the
    lot on navigation.

    `smtp_password` omitted means "keep the stored one". The database CHECK
    `ck_workspace_email_overrides_enabled_is_complete` is what refuses an
    ENABLED row that is not complete, so this schema does not duplicate that
    rule in a second, driftable place.
    """

    is_enabled: bool = False

    smtp_host: str | None = Field(default=None, min_length=1, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, min_length=1, max_length=255)
    smtp_password: str | None = Field(default=None, min_length=1, max_length=255)
    sender_name: str | None = Field(default=None, min_length=1, max_length=100)
    encryption: EmailEncryption = EmailEncryption.TLS

    from_address: EmailStr | None = None
    reply_to_address: EmailStr | None = None


class WorkspaceEmailOverrideResponse(BaseModel):
    """The override as stored. No password field, masked or otherwise."""

    workspace_id: UUID
    organization_id: UUID
    is_enabled: bool
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    sender_name: str | None = None
    encryption: EmailEncryption
    from_address: str | None = None
    reply_to_address: str | None = None
    #: Whether a password is stored, without saying anything about it.
    has_password: bool = False
    updated_by_user_id: Union[UUID, None] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def model_validate(cls, obj, **kwargs):  # type: ignore[override]
        instance = super().model_validate(obj, **kwargs)
        instance.has_password = bool(
            getattr(obj, "smtp_password_encrypted", None)
        )
        return instance


class EmailResolutionLayerResponse(BaseModel):
    """One rung of the ladder and what happened on it."""

    layer: str
    applied: bool
    reason: str | None = None
    detail: str | None = None


class EmailResolutionResponse(BaseModel):
    """The resolved sender and the full explanation behind it."""

    from_address: str
    sender_name: str
    reply_to: str | None = None
    template_namespace: str
    transport_layer: str
    identity_layer: str
    smtp_host: str
    degraded_reason: str | None = None
    trail: list[EmailResolutionLayerResponse]
