"""
Serialization schemas for workspace access grants.

Distinct from organization membership, which is the billable seat.

is_active was replaced by MembershipStatus, which distinguishes "invited but
not yet accepted" from "removed by an administrator" from "temporarily
suspended" — three states a boolean could not tell apart.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.organization import MembershipStatus, OrganizationRole
from app.models.workspace import WorkspaceRole
from app.schemas.organization import UserSummary


# ============================================================================
# Request Schemas
# ============================================================================

class WorkspaceMemberGrant(BaseModel):
    """
    Grants workspace access to an existing organization member.

    The target must already hold an ACTIVE organization membership: the seat is
    what authorizes their presence in the tenant at all.
    """
    user_id: UUID
    role: WorkspaceRole = Field(default=WorkspaceRole.VIEWER)


class WorkspaceMemberRoleUpdate(BaseModel):
    """
    Changes an existing workspace grant.

    Granting or revoking workspace ADMIN requires organization-level standing,
    which resolves the deadlock two workspace admins would otherwise create.
    """
    role: WorkspaceRole


# ============================================================================
# Response Schemas
# ============================================================================

class WorkspaceMemberResponse(BaseModel):
    """
    A workspace access grant.

    is_derived distinguishes a stored grant from an organization administrator
    whose ADMIN role is computed rather than persisted. The client uses it to
    disable revocation controls, since a derived grant cannot be revoked at
    workspace level — it follows from the organization role.
    """
    id: UUID | None = Field(
        default=None,
        description="Membership identifier. Null for derived grants.",
    )
    workspace_id: UUID
    user: UserSummary
    role: WorkspaceRole
    status: MembershipStatus
    is_derived: bool = Field(
        default=False,
        description=(
            "True when this access comes from an organization OWNER or ADMIN "
            "role rather than a stored workspace grant."
        ),
    )
    organization_role: OrganizationRole | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class WorkspaceMemberListResponse(BaseModel):
    """
    Everyone with access to a workspace.

    Merges explicit grants with organization administrators holding derived
    ADMIN. A directory of stored rows alone would omit the people with the most
    access — accurate to the schema, misleading to the user.
    """
    items: list[WorkspaceMemberResponse]
    total: int
