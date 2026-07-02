"""
Data validation and serialization schemas (Pydantic v2) for FlowPilot AI.

Enforces parameter boundaries on user registration and login parameters, 
and maps standardized, secure response payloads.
"""

import uuid
from datetime import datetime
from typing import Union
from pydantic import BaseModel, Field, EmailStr

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