"""
Request validation and serialization schemas for Workspaces.

A workspace is the collaboration boundary and always belongs to an
organization. Locale and branding live here rather than on the organization
because a US and an India workspace on one contract legitimately need different
currency, timezone, and date formatting.

company_name was removed in ARCH-01 and now lives on the organization as
`name`: it was the tenant's identity, not the workspace's. is_active was
replaced by the WorkspaceStatus enum, which distinguishes archived from
suspended where a boolean could not.

Maximum lengths mirror the model exactly, including the wider language bound:
BCP-47 tags such as "sr-Latn-RS-u-ca-gregory" exceed ten characters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.slugs import MAX_SLUG_LENGTH, MIN_SLUG_LENGTH
from app.models.workspace import WorkspaceRole, WorkspaceStatus


# ============================================================================
# Request Schemas
# ============================================================================

class WorkspaceCreate(BaseModel):
    """
    Creates an additional workspace inside an existing organization.

    Distinct from founding an organization, which is an account-level
    capability. Creating a workspace within a tenant is an administrative act
    inside that tenant.
    """
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
                raise ValueError(
                    "Field cannot be empty or contain only whitespace."
                )
            return stripped
        return v


class WorkspaceUpdate(BaseModel):
    """
    Partial workspace update. None means "leave unchanged".

    Clearing the logo uses DELETE /workspaces/{id}/logo rather than passing
    null here, since null is reserved for "unchanged".
    """
    workspace_name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(
        default=None, min_length=MIN_SLUG_LENGTH, max_length=MAX_SLUG_LENGTH
    )
    timezone: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=20)
    currency: str | None = Field(default=None, max_length=10)
    date_format: str | None = Field(default=None, max_length=30)
    company_logo_url: str | None = Field(default=None, max_length=500)

    @field_validator("workspace_name", mode="before")
    @classmethod
    def check_empty_and_whitespace(cls, v: Any) -> Any:
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                raise ValueError(
                    "Field cannot be empty or contain only whitespace."
                )
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
    company_logo_url: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkspaceSummary(BaseModel):
    """
    Compact workspace projection for switchers and bootstrap payloads.

    Carries the caller's effective role so the client can render permission-
    dependent affordances without a request per workspace.
    """
    id: UUID
    organization_id: UUID
    slug: str
    workspace_name: str
    status: WorkspaceStatus
    company_logo_url: str | None = None
    effective_role: WorkspaceRole

    model_config = ConfigDict(from_attributes=True)


class WorkspaceSlugAvailabilityResponse(BaseModel):
    """Advisory availability check, scoped to one organization."""
    slug: str
    available: bool
    reason: str | None = None