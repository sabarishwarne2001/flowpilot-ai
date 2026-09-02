"""
Request validation and serialization schemas for Workspaces.

ARCH-07 Step 7 / ARCH-08 Step 1: company_logo_url is a computed property
derived from logo_file_id, pointing to the authenticated streaming route.
The request column parameter was removed in ARCH-08 Step 1.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.core.slugs import MAX_SLUG_LENGTH, MIN_SLUG_LENGTH
from app.models.workspace import WorkspaceRole, WorkspaceStatus


# ============================================================================
# Request Schemas
# ============================================================================

class WorkspaceCreate(BaseModel):
    workspace_name: str = Field(..., min_length=1, max_length=100)
    slug: str | None = Field(
        default=None,
        min_length=MIN_SLUG_LENGTH,
        max_length=MAX_SLUG_LENGTH,
        description="Derived from the workspace name when omitted.",
    )
    timezone: str = Field(default="UTC", max_length=100)
    language: str = Field(default="en", max_length=20)
    currency: str = Field(default="USD", max_length=10)
    date_format: str = Field(default="YYYY-MM-DD", max_length=30)

    @field_validator("workspace_name", mode="before")
    @classmethod
    def check_empty_and_whitespace(cls, v: Any) -> Any:
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                raise ValueError("Field cannot be empty or contain only whitespace.")
            return stripped
        return v


class WorkspaceUpdate(BaseModel):
    workspace_name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(
        default=None, min_length=MIN_SLUG_LENGTH, max_length=MAX_SLUG_LENGTH
    )
    timezone: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=20)
    currency: str | None = Field(default=None, max_length=10)
    date_format: str | None = Field(default=None, max_length=30)

    @field_validator("workspace_name", mode="before")
    @classmethod
    def check_empty_and_whitespace(cls, v: Any) -> Any:
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                raise ValueError("Field cannot be empty or contain only whitespace.")
            return stripped
        return v


# ============================================================================
# Response Schemas
# ============================================================================

class WorkspaceResponse(BaseModel):
    """Serialized workspace configuration returned to the frontend."""
    id: UUID
    organization_id: UUID
    slug: str
    workspace_name: str
    status: WorkspaceStatus
    timezone: str
    language: str
    currency: str
    date_format: str
    logo_file_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def company_logo_url(self) -> Optional[str]:
        """Derived from logo_file_id, pointing to authenticated streaming route."""
        if self.logo_file_id is None:
            return None
        return f"/api/v1/workspaces/{self.id}/logo"


class WorkspaceSummary(BaseModel):
    """Compact workspace projection for switchers and bootstrap payloads."""
    id: UUID
    organization_id: UUID
    slug: str
    workspace_name: str
    status: WorkspaceStatus
    logo_file_id: Optional[UUID] = None
    effective_role: WorkspaceRole

    model_config = ConfigDict(from_attributes=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def company_logo_url(self) -> Optional[str]:
        if self.logo_file_id is None:
            return None
        return f"/api/v1/workspaces/{self.id}/logo"


class WorkspaceSlugAvailabilityResponse(BaseModel):
    slug: str
    available: bool
    reason: str | None = None
