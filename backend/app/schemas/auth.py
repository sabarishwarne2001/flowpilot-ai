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