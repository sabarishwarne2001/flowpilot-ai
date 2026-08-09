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

    # Exposed so the client can render the verification banner and route
    # around the tenant gate before the server has to refuse a request. NULL
    # means unverified and is a permanent, meaningful value — the account is
    # usable, it simply cannot reach a workspace yet (§B.4).
    email_verified_at: Union[datetime, None] = None

    model_config = {
        "from_attributes": True  # Instructs Pydantic v2 to load data from SQLAlchemy objects
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

    Deliberately carries no token and no hash. A device list is a read-only
    view; anything in it that could be replayed would turn the page that shows
    a user their sessions into the page that gives them away.

    last_used_at is None until the session's first refresh, which for a device
    signed in within the last ten minutes is the normal state rather than an
    error.
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

    A POST body rather than a query parameter (§B.9). The token reaches the
    frontend in the fragment, which no server ever sees; sending it back in a
    body keeps it out of access logs and Referer headers on the way in too.
    """

    token: str = Field(..., min_length=1, description="Verification token.")


class VerificationStatusResponse(BaseModel):
    """
    The outcome of a verification attempt.

    already_verified distinguishes "your link worked" from "you were already
    verified". Both are successes from the user's side — clicking a link twice
    should not look like an error — but the UI wording differs.
    """

    email: EmailStr
    email_verified_at: datetime
    already_verified: bool = False


class ResendVerificationResponse(BaseModel):
    """
    Acknowledgement of a resend request.

    delivered is False when the message could not be sent. The request still
    succeeds: an SMTP outage must not present as a broken account (R7), and the
    user can try again.
    """

    delivered: bool
    detail: str


class ForgotPasswordRequest(BaseModel):
    """
    Requests a reset link for an address.

    Anonymous. The response is identical whether or not the address matches an
    account, so nothing here can be used to test for membership.
    """

    email: EmailStr = Field(..., max_length=255)


class ResetPasswordRequest(BaseModel):
    """
    Completes a reset with a token read from the URL fragment.

    min_length matches registration's. Enforcing a floor at reset but not at
    signup — or the reverse — leaves accounts whose password could not be set
    today, which is the kind of inconsistency that surfaces as a confusing
    validation error years later.
    """

    token: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    """
    Replaces a password the caller already knows.

    The current password is required despite the caller being authenticated:
    an access token is a bearer credential that may have been taken, and this
    is what stops a stolen session from locking the real owner out.
    """

    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class PasswordActionResponse(BaseModel):
    """
    Acknowledgement of a completed password action.

    sessions_revoked is reported so the client can say what just happened
    rather than leaving a user to discover their other devices are signed out.
    """

    detail: str
    sessions_revoked: bool = True


class RegistrationAcknowledgement(BaseModel):
    """
    The response to every registration attempt.

    Carries no user object and no identifier, because in one of the two
    branches there is no account this caller is entitled to know about. That
    absence is the schema doing its job: a field that existed only when the
    address was free would be the enumeration oracle again, in a different
    shape.
    """

    detail: str