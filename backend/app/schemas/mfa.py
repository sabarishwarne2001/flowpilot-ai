"""N-017 — request and response bodies for two-factor sign-in settings."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MfaStatusResponse(BaseModel):
    enabled: bool
    pending: bool
    confirmed_at: Optional[datetime] = None
    recovery_codes_remaining: int = 0


class MfaSetupRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class MfaSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class MfaDisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=6, max_length=32)


class MfaRecoveryCodesResponse(BaseModel):
    recovery_codes: list[str]
