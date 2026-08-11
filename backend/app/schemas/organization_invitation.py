"""
Request and response schemas for the ARCH-04 invitation lifecycle.

Two grant shapes exist deliberately (§D7.5): WorkspaceGrantResponse carries a
workspace_id for the administrative views; AcceptedGrantSummary does not,
because it is built from GrantLine (app.templates.emails.common), which was
designed for a courtesy notice and has never carried an id.

token_hash appears in no schema here, matching WorkspaceInvitationResponse's
existing precedent. The plaintext token exists only in the mail dispatch.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.core.config import settings
from app.models.organization import OrganizationRole
from app.models.organization_invitation import (
    InvitationStatus,
    InvitationWorkspaceGrant,
    OrganizationInvitation,
)
from app.models.workspace import WorkspaceRole


# ============================================================================
# Request Schemas
# ============================================================================

class WorkspaceGrantInput(BaseModel):
    """One workspace-and-role pair requested on an invitation."""
    workspace_id: UUID
    role: WorkspaceRole


class OrganizationInvitationCreate(BaseModel):
    """
    Input to issue an invitation.

    grants may be empty — the §B.1 BILLING case, an organization seat with no
    workspace access at all. Capped at INVITATION_MAX_GRANTS at the schema
    layer so an oversized payload is rejected before it reaches the service;
    the service re-validates tenancy and status per grant regardless.
    """
    email: EmailStr = Field(
        ..., description="The address to invite."
    )
    organization_role: OrganizationRole = Field(
        default=OrganizationRole.MEMBER,
        description=(
            "ADMIN, BILLING, or MEMBER. OWNER is not invitable — see "
            "ARCH-04 §B.4."
        ),
    )
    grants: list[WorkspaceGrantInput] = Field(
        default_factory=list,
        max_length=settings.INVITATION_MAX_GRANTS,
        description="Workspace grants provisioned on acceptance. May be empty.",
    )


class OrganizationInvitationTokenRequest(BaseModel):
    """
    Processes an invitation via its secure token.

    The token identifies the invitation; the authenticated session identifies
    the actor. Both are required for accept and reject — a token alone is not
    proof of identity.
    """
    token: str = Field(..., min_length=1)


# ============================================================================
# Response Schemas
# ============================================================================

class WorkspaceGrantResponse(BaseModel):
    """A workspace grant, as shown to an administrator managing invitations."""
    workspace_id: UUID
    workspace_name: str
    role: WorkspaceRole

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def resolve_workspace_name(cls, data: Any) -> Any:
        """
        ORM validator mapping workspace_name cleanly from grant.workspace
        before validation occurs.
        """
        if hasattr(data, "workspace") and data.workspace is not None:
            return {
                "workspace_id": data.workspace_id,
                "workspace_name": data.workspace.workspace_name,
                "role": data.role,
            }
        return data

    @classmethod
    def from_orm_grant(cls, grant: InvitationWorkspaceGrant) -> "WorkspaceGrantResponse | None":
        """
        Returns None for a grant whose workspace has been deleted since
        issuance (§B.2) — the caller filters these out rather than rendering
        a grant that points at nothing.
        """
        if grant.workspace is None:
            return None
        return cls.model_validate(grant)


class AcceptedGrantSummary(BaseModel):
    """
    A grant as shown on the acceptance response. See §D7.5 — deliberately
    narrower than WorkspaceGrantResponse; built from GrantLine, which carries
    no id.
    """
    workspace_name: str
    role: str


class OrganizationInvitationResponse(BaseModel):
    """
    Serialized invitation for administrative views.

    No token_hash field, matching WorkspaceInvitationResponse's precedent —
    the API never returns the credential, even to the inviter.
    """
    id: UUID
    organization_id: UUID
    inviter_id: UUID
    invited_user_id: UUID | None = None
    email: str
    organization_role: OrganizationRole
    status: InvitationStatus
    expires_at: datetime
    accepted_at: datetime | None = None
    rejected_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_by_id: UUID | None = None
    last_sent_at: datetime | None = None
    send_count: int
    created_at: datetime
    updated_at: datetime
    grants: list[WorkspaceGrantResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_orm_invitation(
        cls, invitation: OrganizationInvitation
    ) -> "OrganizationInvitationResponse":
        """
        Builds the response including grants.
        """
        grants_mapped = [
            g for g in (
                WorkspaceGrantResponse.from_orm_grant(grant)
                for grant in invitation.grants
            ) if g is not None
        ]
        data = {
            "id": invitation.id,
            "organization_id": invitation.organization_id,
            "inviter_id": invitation.inviter_id,
            "invited_user_id": invitation.invited_user_id,
            "email": invitation.email,
            "organization_role": invitation.organization_role,
            "status": invitation.status,
            "expires_at": invitation.expires_at,
            "accepted_at": invitation.accepted_at,
            "rejected_at": invitation.rejected_at,
            "revoked_at": invitation.revoked_at,
            "revoked_by_id": invitation.revoked_by_id,
            "last_sent_at": invitation.last_sent_at,
            "send_count": invitation.send_count,
            "created_at": invitation.created_at,
            "updated_at": invitation.updated_at,
            "grants": grants_mapped,
        }
        return cls.model_validate(data)


class OrganizationInvitationListResponse(BaseModel):
    items: list[OrganizationInvitationResponse]
    total: int


class WorkspacePreviewEntry(BaseModel):
    """One workspace grant as shown on the public, unauthenticated preview."""
    name: str
    role: WorkspaceRole


class OrganizationInvitationPreviewResponse(BaseModel):
    """
    Public preview, resolved from a token, served with no authentication.

    Carries no database identifiers — a recipient without an account yet sees
    only what they need to decide whether to accept.
    """
    organization_name: str
    inviter_email: str
    invited_email: str
    organization_role: OrganizationRole
    workspaces: list[WorkspacePreviewEntry]
    expires_at: datetime


class OrganizationInvitationAcceptResponse(BaseModel):
    """
    Result of accepting an invitation.

    Carries organization_slug so the client can navigate straight to the new
    workspace/org without a follow-up bootstrap call — see §0.1, the carry-in
    that made this field available.
    """
    invitation_id: UUID
    organization_id: UUID
    organization_slug: str
    organization_role: OrganizationRole
    provisioned_grants: list[AcceptedGrantSummary]
    skipped_grant_count: int


class MyPendingInvitation(BaseModel):
    """
    One pending invitation as shown on /me/invitations. §D7.4 — deliberately
    informational: no id, no token, nothing actionable. The plaintext token
    was never persisted, so this cannot offer a working accept link regardless of shape.
    """
    organization_name: str
    organization_role: OrganizationRole
    inviter_email: str
    workspaces: list[WorkspacePreviewEntry]
    expires_at: datetime


class MyPendingInvitationsResponse(BaseModel):
    items: list[MyPendingInvitation]


# ============================================================================
# API Router Aliases
# ============================================================================

InvitationCreateRequest = OrganizationInvitationCreate
InvitationResponse = OrganizationInvitationResponse
InvitationPreviewResponse = OrganizationInvitationPreviewResponse