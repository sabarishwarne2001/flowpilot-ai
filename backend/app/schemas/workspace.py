from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import HttpUrl


# ============================================================================
# Base
# ============================================================================

class WorkspaceBase(BaseModel):
    """
    Shared workspace configuration fields.
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

    company_logo_url: HttpUrl | None = None

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

    primary_color: str = Field(
        default="#2563EB",
        max_length=20,
    )

    secondary_color: str = Field(
        default="#0F172A",
        max_length=20,
    )

    is_active: bool = True


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

    company_logo_url: HttpUrl | None = None

    timezone: str | None = Field(default=None, max_length=100)

    language: str | None = Field(default=None, max_length=20)

    currency: str | None = Field(default=None, max_length=10)

    date_format: str | None = Field(default=None, max_length=30)

    primary_color: str | None = Field(default=None, max_length=20)

    secondary_color: str | None = Field(default=None, max_length=20)

    is_active: bool | None = None


# ============================================================================
# Response
# ============================================================================

class WorkspaceResponse(WorkspaceBase):
    """
    Serialized workspace configuration returned to the frontend.
    """

    id: UUID

    user_id: UUID

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )