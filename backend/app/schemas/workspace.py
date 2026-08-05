from datetime import datetime
from uuid import UUID
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkspaceBase(BaseModel):
    """
    Shared workspace configuration fields with whitespace-check validators.
    """

    workspace_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
    )

    company_name: str = Field(
        ...,
        min_length=1,
        max_length=150,
    )

    company_logo_url: str | None = None

    timezone: str = Field(
        default="UTC",
        max_length=100,
    )

    language: str = Field(
        default="en",
        max_length=20,
    )

    currency: str = Field(
        default="USD",
        max_length=10,
    )

    date_format: str = Field(
        default="YYYY-MM-DD",
        max_length=30,
    )

    is_active: bool = True

    @field_validator("workspace_name", "company_name", mode="before")
    @classmethod
    def check_empty_and_whitespace(cls, v: Any) -> Any:
        if isinstance(v, str):
            v_stripped = v.strip()
            if not v_stripped:
                raise ValueError("Field cannot be empty or contain only whitespace.")
            return v_stripped
        return v


# ============================================================================
# Create
# ============================================================================

class WorkspaceCreate(WorkspaceBase):
    """
    Initial workspace configuration.
    """

    pass


# ============================================================================
# Update
# ============================================================================

class WorkspaceUpdate(BaseModel):
    """
    Partial workspace update.
    """

    workspace_name: str | None = Field(default=None, min_length=1, max_length=100)

    company_name: str | None = Field(default=None, min_length=1, max_length=150)

    company_logo_url: str | None = None

    timezone: str | None = Field(default=None, max_length=100)

    language: str | None = Field(default=None, max_length=20)

    currency: str | None = Field(default=None, max_length=10)

    date_format: str | None = Field(default=None, max_length=30)

    is_active: bool | None = None


# ============================================================================
# Response
# ============================================================================

class WorkspaceResponse(WorkspaceBase):
    """
    Serialized workspace configuration returned to the frontend.
    """

    id: UUID

    # DEPRECATED. Null for workspaces created after revision c4e81a9f2b73.
    user_id: UUID | None = None

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )