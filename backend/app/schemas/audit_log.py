"""
Audit log read contracts for FlowPilot AI (ARCH-07 Step 4, ARCH-08 Step 2).
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
    """ARCH-08 Step 2 (§B.9).

    `total` and `offset` were removed. Keyset cursor pagination uses has_more and
    next_cursor to achieve O(1) page access regardless of table depth.
    """
    items: list[AuditLogRead]
    limit: int
    has_more: bool = Field(
        description="True when at least one further row matches the filters."
    )
    next_cursor: Optional[str] = Field(
        default=None,
        description=(
            "Opaque cursor for the following page. Present iff has_more. "
            "Pass verbatim as ?cursor=; do not construct or modify."
        ),
    )


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

    def digest_scope(self, *, organization_id: uuid.UUID) -> dict[str, Any]:
        """Everything that changes the result set. Binds the cursor to it."""
        return {
            "organization_id": organization_id,
            "resource_type": self.resource_type,
            "action": self.action,
            "actor_id": self.actor_id,
            "resource_id": self.resource_id,
            "workspace_id": self.workspace_id,
            "date_from": self.date_from,
            "date_to": self.date_to,
        }