from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.workspace import WorkspaceRole
from app.models.workspace_invitation import InvitationStatus


# ============================================================================
# Request Schemas
# ============================================================================

class WorkspaceInvitationCreate(BaseModel):
    """
    Schema representing the input parameters required to invite a user 
    to a workspace.
    """
    email: str = Field(
        ...,
        min_length=3,
        max_length=255,
        description="The email address of the user to be invited."
    )
    role: WorkspaceRole = Field(
        default=WorkspaceRole.VIEWER,
        description="The target membership role to be assigned upon invitation acceptance."
    )


class WorkspaceInvitationTokenRequest(BaseModel):
    """
    Schema for processing an invitation via its secure token.
    """
    token: str = Field(
        ...,
        min_length=1,
        description="The secure URL-safe invitation token.",
    )


# ============================================================================
# Response Serialization Schema
# ============================================================================

class WorkspaceInvitationResponse(BaseModel):
    """
    Serialized workspace invitation representation returned to API clients.
    """
    id: UUID
    workspace_id: UUID
    inviter_id: UUID
    email: str
    role: WorkspaceRole
    status: InvitationStatus
    expires_at: datetime
    accepted_at: datetime | None = None
    rejected_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


# ============================================================================
# Collection List Wrapper
# ============================================================================

class WorkspaceInvitationListResponse(BaseModel):
    """
    Strongly typed collection wrapper for workspace invitation responses.
    """
    invitations: list[WorkspaceInvitationResponse]

    model_config = ConfigDict(
        from_attributes=True,
    )