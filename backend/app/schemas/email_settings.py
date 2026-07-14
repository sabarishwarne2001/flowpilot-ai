"""
Pydantic request and response schemas for Email Settings.

Defines the API contracts used to manage user SMTP configuration,
including validation rules and serialization behavior.
"""

from enum import Enum

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

    user_id: UUID

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