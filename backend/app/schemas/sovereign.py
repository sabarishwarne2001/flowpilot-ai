"""ARCH50-S1:schemas — the Sovereign Edition's API shapes (tenant egress lockdown, operator console)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

TenantChannel = Literal["WEBHOOK", "WAREHOUSE", "ERP_HTTP", "ERP_SFTP", "IDENTITY", "SMTP_TENANT", "LLM_PROVIDER"]
Channel = Literal["WEBHOOK", "WAREHOUSE", "ERP_HTTP", "ERP_SFTP", "IDENTITY", "SMTP_TENANT", "LLM_PROVIDER",
                  "SMTP_PLATFORM", "LLM_LOCAL", "STORAGE", "BILLING", "INTERNAL", "DNS", "BACKUP"]


class EgressRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Optional[TenantChannel] = None
    host_pattern: str = Field(min_length=1, max_length=253)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    note: Optional[str] = Field(default=None, max_length=200)


class EgressRuleOut(BaseModel):
    id: uuid.UUID
    channel: Optional[str] = None
    host_pattern: str
    port: Optional[int] = None
    note: Optional[str] = None
    created_at: datetime
    created_by_user_id: Optional[uuid.UUID] = None


class EgressPolicyOut(BaseModel):
    organization_id: uuid.UUID
    lockdown_enabled: bool
    updated_at: Optional[datetime] = None
    updated_by_user_id: Optional[uuid.UUID] = None
    rules: list[EgressRuleOut]
    max_rules: int
    deployment_mode: str
    governed_channels: list[str]


class EgressLockdownIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lockdown_enabled: bool


class EgressRefusalOut(BaseModel):
    id: uuid.UUID
    organization_id: Optional[uuid.UUID] = None
    channel: str
    host: str
    port: int
    reason: str
    mode: str
    bucket_start: datetime
    first_at: datetime
    last_at: datetime
    count: int


class EgressRefusalList(BaseModel):
    days: int
    refusals: list[EgressRefusalOut]


class EgressTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Channel
    destination: str = Field(min_length=1, max_length=2048)


class EgressDecisionOut(BaseModel):
    allowed: bool
    channel: str
    host: str
    port: Optional[int] = None
    mode: str
    reason: Optional[str] = None
    explanation: str
    organization_id: Optional[str] = None
    matched: Optional[str] = None


class OperatorEgressTestIn(EgressTestIn):
    organization_id: Optional[uuid.UUID] = None


class LicenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    licence: str = Field(min_length=2, max_length=65536)


class LicenceStatusOut(BaseModel):
    status: str
    edition: str
    usable: bool
    reason: str = ""
    licence_id: Optional[str] = None
    licensee: Optional[str] = None
    key_id: Optional[str] = None
    expires_at: Optional[str] = None
    grace_until: Optional[str] = None
    days_left: Optional[int] = None
    max_organizations: Optional[int] = None
    max_seats: Optional[int] = None
    features: list[str] = []
    source: Optional[str] = None


class SovereignStatusOut(BaseModel):
    edition: str
    environment: str
    egress: dict[str, Any]
    local_llm: dict[str, Any]
    licence: LicenceStatusOut
    usage: dict[str, int]
    dr: dict[str, Any]
    refusals_24h: int


class ReleaseStatusOut(BaseModel):
    status: str
    release_dir: str
    files: int
    modified: list[str]
    missing: list[str]
    key_id: Optional[str] = None
    release: Optional[dict[str, Any]] = None
