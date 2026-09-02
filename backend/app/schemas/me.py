"""
Bootstrap context schemas.

GET /me/context is the single call that tells the client who the actor is,
which tenants they belong to, and where to send them.

It exists to make three states distinguishable that the pre-ARCH-01 frontend
could not tell apart. OnboardingGuard called getWorkspace() and treated any
falsy result as "no workspace", so an expired token and a genuinely
membership-less user produced the same signal — and session expiry sent people
to "Create My Workspace" instead of the login page. Removal from a workspace
did the same, which is how removed members ended up founding phantom
organizations.

Now:
    401                      -> token invalid or expired    -> /login
    200, organizations: []   -> authenticated, no tenant    -> /onboarding
    200, organizations: [..] -> normal                      -> the workspace
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.organization import OrganizationRole, OrganizationStatus
from app.schemas.workspace import WorkspaceSummary


class MeUser(BaseModel):
    """
    The authenticated actor.

    ARCH-05 Step 5: carries `display_name` and `timezone` now that `users`
    has them — this is the payload the sidebar and header render from on
    every session start, and "the sidebar renders `user?.email`" was one of
    A.2.4's own named symptoms. `locale` is intentionally absent: nothing at
    boot time needs it yet, and `GET /me/profile` (`UserProfileResponse`,
    `app/schemas/user.py`) is the fuller, deliberately-opened profile
    contract for a settings screen. Keeping this projection minimal is the
    same reasoning `UserProfileResponse`'s docstring gives for NOT reusing
    this class there: a field added here for bootstrap reasons should not
    show up on the settings page for no reason, and vice versa.
    """
    id: UUID
    email: EmailStr
    is_active: bool
    display_name: str | None = None
    timezone: str | None = None

    model_config = ConfigDict(from_attributes=True)


class OrganizationMembershipSummary(BaseModel):
    """
    One organization the actor belongs to, with their standing and reachable
    workspaces.

    Workspaces are embedded rather than fetched per organization so the client
    can render the full switcher from a single response.
    """
    organization_id: UUID
    organization_slug: str
    organization_name: str
    organization_status: OrganizationStatus
    role: OrganizationRole
    workspaces: list[WorkspaceSummary] = Field(default_factory=list)


class MeContextResponse(BaseModel):
    """
    Everything the client needs on boot, in one round trip.

    default_organization_id and default_workspace_id resolve the landing
    destination server-side. Returning null for both alongside an empty
    organizations list is the unambiguous "send this user to onboarding"
    signal — reachable only with a valid session, which is precisely the
    distinction that was missing before.
    """
    user: MeUser
    organizations: list[OrganizationMembershipSummary] = Field(
        default_factory=list
    )
    default_organization_id: UUID | None = None
    default_workspace_id: UUID | None = None
    requires_onboarding: bool = Field(
        ...,
        description=(
            "True when the actor belongs to no organization and should be "
            "sent to tenant creation. Never inferred from a failed request."
        ),
    )
