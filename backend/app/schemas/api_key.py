"""API Key validation and serialization contracts (ARCH-08 §9.6)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.core.scopes import ApiKeyScope


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    scopes: list[ApiKeyScope] = Field(..., min_items=1)
    expires_at: Optional[datetime] = None


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    name: str
    scopes: list[str]
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    deactivated_at: Optional[datetime] = None
    deactivated_reason: Optional[str] = None
    previous_secret_expires_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ApiKeyResponse(BaseModel):
    """Returned ONCE upon issuance. Contains the full token string."""
    api_key: ApiKeyRead
    token: str = Field(description="Full token string. Shown ONCE. Never stored or logged.")


class ApiKeyRotateRequest(BaseModel):
    force: bool = Field(default=False, description="Force rotation even if a dual-secret overlap is currently active.")
