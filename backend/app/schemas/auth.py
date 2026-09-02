"""
Data validation and serialization schemas (Pydantic v2) for FlowPilot AI.

Enforces parameter boundaries on user registration and login parameters, 
and maps standardized, secure response payloads.
"""

import uuid
from datetime import datetime
from typing import Union
from pydantic import BaseModel, ConfigDict, Field, EmailStr

class UserBase(BaseModel):
    """
    Base properties shared across user validation schemas.
    """
    email: EmailStr = Field(..., max_length=255, description="Unique identity email address.")

class UserRegister(UserBase):
    """
    Validation schema used to process signup registration requests.
    """
    password: str = Field(..., min_length=8, max_length=128, description="Plaintext security password.")

    redirect: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Optional post-verification destination (ARCH-06 §B.8, Option A). "
            "Accepted here without validation and validated by "
            "app.core.redirects.sanitize_redirect_path before it is embedded "
            "in any link -- an unsafe value is dropped, never rejected, so a "
            "hostile redirect cannot be used to block a legitimate signup."
        ),
    )

class UserLogin(UserBase):
    """
    Validation schema used to process authentication sign-in requests.
    """
    password: str = Field(..., description="Plaintext security password.")

class UserResponse(UserBase):
    """
    Serialization schema representing user records returned to clients.
    
    Protects sensitive metadata by completely excluding credentials.
    """
    id: uuid.UUID
    is_active: bool
    is_superuser: bool
    created_at: datetime
    updated_at: datetime

    email_verified_at: Union[datetime, None] = None

    model_config = {
        "from_attributes": True
    }

class TokenResponse(BaseModel):
    """
    Serialization schema returned upon successful user authentication.
    """
    access_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    """
    Payload structure extracted from decoded JWT access tokens.
    """
    sub: Union[uuid.UUID, None] = None


class SessionResponse(BaseModel):
    """
    One live refresh session, as shown in the device list.
    """

    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    last_used_at: Union[datetime, None] = None
    ip_address: Union[str, None] = None
    user_agent: Union[str, None] = None

    model_config = ConfigDict(from_attributes=True)


class VerifyEmailRequest(BaseModel):
    """
    Submits a verification token read from the URL fragment.
    """

    token: str = Field(..., min_length=1, description="Verification token.")


class VerificationStatusResponse(BaseModel):
    """
    The outcome of a verification attempt.
    """

    email: EmailStr
    email_verified_at: datetime
    already_verified: bool = False


class ResendVerificationResponse(BaseModel):
    """
    Acknowledgement of a resend request.
    """

    delivered: bool
    detail: str


class ForgotPasswordRequest(BaseModel):
    """
    Requests a reset link for an address.
    """

    email: EmailStr = Field(..., max_length=255)


class ResetPasswordRequest(BaseModel):
    """
    Completes a reset with a token read from the URL fragment.
    """

    token: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    """
    Replaces a password the caller already knows.
    """

    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class PasswordActionResponse(BaseModel):
    """
    Acknowledgement of a completed password action.
    """

    detail: str
    sessions_revoked: bool = True


class RegistrationAcknowledgement(BaseModel):
    """
    The response to every registration attempt.
    """

    detail: str
