"""
Request validation and serialization schemas for workspace invitations.

An invitation grants access to one workspace inside one organization.
Acceptance provisions both an organization seat and a workspace grant, because
organization membership is a precondition for any workspace access.

The preview schema is deliberately narrow. It is served to unauthenticated
visitors holding a token, so it exposes only what a recipient needs to decide
whether to accept: who invited them, to what, at what role, and until when. No
database identifiers appear in it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.workspace import WorkspaceRole
from app.models.workspace_invitation import InvitationStatus


# ============================================================================
# Request Schemas
# ============================================================================

class WorkspaceInvitationCreate(BaseModel):
    """
    Input parameters required to invite a user to a workspace.

    The role is validated against the inviter's own standing at the service
    layer. Before ARCH-01 it was not: a Manager could invite at OWNER level and
    self-escalate, because the API accepted the full enum while only the
    frontend dropdown restricted the options.
    """
    email: EmailStr = Field(
        ...,
        description="The validated email address of the user to be invited.",
    )
    role: WorkspaceRole = Field(
        default=WorkspaceRole.VIEWER,
        description=(
            "Workspace role assigned on acceptance. Must sit at or below the "
            "inviter's own authority."
        ),
    )


class WorkspaceInvitationTokenRequest(BaseModel):
    """
    Processes an invitation via its secure token.

    The token identifies the invitation. The authenticated session identifies
    the actor. Both are required to accept or reject: a token alone is not
    proof of identity, which is why the pre-ARCH-01 unauthenticated endpoints
    allowed any token holder to act on the invitee's behalf.
    """
    token: str = Field(
        ...,
        min_length=1,
        description="The secure URL-safe invitation token.",
    )


# ============================================================================
# Response Schemas
# ============================================================================

class WorkspaceInvitationResponse(BaseModel):
    """Serialized workspace invitation returned to authenticated API clients."""
    id: UUID
    workspace_id: UUID
    organization_id: UUID | None = None
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

    model_config = ConfigDict(from_attributes=True)


class WorkspaceInvitationPreviewResponse(BaseModel):
    """
    Public preview of an invitation, resolved from its token.

    Served without authentication so a recipient can see what they are being
    asked to join before creating an account. Carries no database identifiers.
    """
    organization_name: str
    workspace_name: str
    inviter_email: str
    invited_email: str
    role: WorkspaceRole
    expires_at: datetime


class WorkspaceInvitationAcceptResponse(BaseModel):
    """
    Result of accepting an invitation.

    Returns the destination alongside the invitation so the client can navigate
    straight into the workspace without a follow-up bootstrap call.
    """
    invitation: WorkspaceInvitationResponse
    organization_id: UUID
    organization_slug: str
    workspace_id: UUID
    workspace_slug: str
    workspace_role: WorkspaceRole