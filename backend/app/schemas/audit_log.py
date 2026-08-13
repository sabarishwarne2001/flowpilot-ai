"""
Audit log read contracts for FlowPilot AI (ARCH-07 Step 4).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.audit_log import AuditAction, AuditResourceType

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime

    organization_id: uuid.UUID
    workspace_id: Optional[uuid.UUID] = None

    actor_id: Optional[uuid.UUID] = None
    resource_type: AuditResourceType
    resource_id: Optional[uuid.UUID] = None
    action: AuditAction

    details: Optional[dict[str, Any]] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


class AuditLogPage(BaseModel):
    items: list[AuditLogRead]
    total: int = Field(description="Total rows matching the filters, ignoring paging.")
    limit: int
    offset: int


class AuditLogFilters(BaseModel):
    resource_type: Optional[AuditResourceType] = None
    action: Optional[AuditAction] = None
    actor_id: Optional[uuid.UUID] = None
    resource_id: Optional[uuid.UUID] = None
    workspace_id: Optional[uuid.UUID] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None

    @field_validator("date_to")
    @classmethod
    def _range_is_ordered(cls, value: Optional[datetime], info: Any) -> Optional[datetime]:
        start = info.data.get("date_from")
        if value is not None and start is not None and value < start:
            raise ValueError("date_to must not precede date_from")
        return value