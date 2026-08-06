"""
Workspace-level authorization logic for FlowPilot AI.

Governs operational access: documents, conversations, the AI assistant,
automation, and workspace settings. Commercial and identity concerns are
governed by app/core/organization_permissions.py.

The central function is resolve_effective_workspace_role. Organization OWNER
and ADMIN receive an implicit ADMIN grant on every workspace in their
organization. That elevation is DERIVED at request time and never written to
workspace_members, because a stored grant would desynchronize the instant an
organization role changed: demote an org ADMIN to MEMBER and stale workspace
ADMIN rows would silently retain full access. GitHub, Slack, and Linear all
derive for the same reason.

WorkspaceRole has no OWNER. A workspace does not own itself; the organization
owns it. Retaining a workspace-level owner would create two competing
ownership concepts, and every future billing or deletion question would have
to disambiguate them.

Scope boundary: roles only. These functions never inspect MembershipStatus or
tenant status. A DEACTIVATED grant is excluded by the query layer and the
request context before any role reaches this module.
"""

from __future__ import annotations

from app.core.organization_permissions import IMPLICIT_WORKSPACE_ADMIN_ROLES
from app.models.organization import OrganizationRole
from app.models.workspace import WorkspaceRole

# ===========================================================================
# Precedence
# ===========================================================================

#: Workspace access ordering. Unlike the organization tier, this IS a true
#: ladder: every capability of a lower role is held by a higher one.
WORKSPACE_ROLE_PRECEDENCE: dict[WorkspaceRole, int] = {
    WorkspaceRole.ADMIN: 3,
    WorkspaceRole.CONTRIBUTOR: 2,
    WorkspaceRole.VIEWER: 1,
}

#: Roles a workspace ADMIN may grant directly. Granting or revoking workspace
#: ADMIN is reserved to organization administrators; see
#: can_assign_workspace_role.
WORKSPACE_ADMIN_ASSIGNABLE_ROLES: frozenset[WorkspaceRole] = frozenset(
    {WorkspaceRole.CONTRIBUTOR, WorkspaceRole.VIEWER}
)


def precedence(role: WorkspaceRole) -> int:
    """Returns the access precedence weight of a workspace role."""
    return WORKSPACE_ROLE_PRECEDENCE[role]


def is_at_least(role: WorkspaceRole, minimum: WorkspaceRole) -> bool:
    """
    Returns True if the role meets or exceeds the minimum required role.

    Safe to use for capability checks at this tier, because workspace roles
    form a genuine ladder. The organization tier deliberately does not offer an
    equivalent, since BILLING breaks that property there.
    """
    return precedence(role) >= precedence(minimum)


# ===========================================================================
# Effective role resolution
# ===========================================================================

def resolve_effective_workspace_role(
    organization_role: OrganizationRole | None,
    workspace_role: WorkspaceRole | None,
) -> WorkspaceRole | None:
    """
    Resolves the role an actor effectively holds in a workspace.

    This is the single authority on workspace access in FlowPilot AI. Every
    authorization decision downstream consumes its result.

    Resolution:
      1. No active organization membership -> None. Organization membership is
         a precondition for any workspace access; a workspace grant without one
         is an invariant violation, not an access path.
      2. Organization OWNER or ADMIN -> WorkspaceRole.ADMIN, derived, whether
         or not an explicit grant exists.
      3. Otherwise -> the explicit grant, which may be None.

    BILLING and MEMBER both fall through to case 3. A finance controller
    holding BILLING with no workspace grant resolves to None and has no
    workspace access at all, which is the purpose of the role.

    Args:
        organization_role: The actor's role in the workspace's parent
            organization, or None if they hold no active membership.
        workspace_role: The actor's explicit workspace grant, or None.

    Returns:
        The effective role, or None if the actor has no access.
    """
    if organization_role is None:
        return None

    if organization_role in IMPLICIT_WORKSPACE_ADMIN_ROLES:
        return WorkspaceRole.ADMIN

    return workspace_role


def has_workspace_access(
    organization_role: OrganizationRole | None,
    workspace_role: WorkspaceRole | None,
) -> bool:
    """
    Whether the actor has any access at all to the workspace.

    A False result maps to HTTP 404, not 403: acknowledging that a workspace
    exists to someone with no access is an enumeration oracle. See
    app/core/exception_handlers.py.
    """
    return (
        resolve_effective_workspace_role(organization_role, workspace_role)
        is not None
    )


# ===========================================================================
# Content capabilities
# ===========================================================================

def can_view_content(role: WorkspaceRole) -> bool:
    """Whether the role may read documents, work items, and conversations."""
    return is_at_least(role, WorkspaceRole.VIEWER)


def can_create_content(role: WorkspaceRole) -> bool:
    """Whether the role may upload documents and create work items."""
    return is_at_least(role, WorkspaceRole.CONTRIBUTOR)


def can_edit_own_content(role: WorkspaceRole) -> bool:
    """Whether the role may edit or delete content it created."""
    return is_at_least(role, WorkspaceRole.CONTRIBUTOR)


def can_edit_any_content(role: WorkspaceRole) -> bool:
    """
    Whether the role may edit or delete content created by other members.

    Restricted to ADMIN. Contributors own their own work; overriding a
    colleague's document is an administrative act.
    """
    return is_at_least(role, WorkspaceRole.ADMIN)


def can_use_assistant(role: WorkspaceRole) -> bool:
    """
    Whether the role may query the AI assistant.

    CONTRIBUTOR and above. Assistant queries consume metered capacity and
    create conversation records, which makes them a write-shaped action even
    though the user experiences them as reading.
    """
    return is_at_least(role, WorkspaceRole.CONTRIBUTOR)


def can_manage_automation(role: WorkspaceRole) -> bool:
    """
    Whether the role may create or modify automation rules.

    ADMIN only. An automation rule acts on behalf of the whole workspace and
    executes without further review, so authoring one is equivalent to
    delegating a standing permission.
    """
    return is_at_least(role, WorkspaceRole.ADMIN)


def can_export_data(role: WorkspaceRole) -> bool:
    """
    Whether the role may bulk-export workspace data.

    ADMIN only. Bulk egress is the action most worth constraining in a
    document platform, and it is distinct from reading individual documents.
    ARCH-06 makes this a per-workspace capability toggle.
    """
    return is_at_least(role, WorkspaceRole.ADMIN)


# ===========================================================================
# Workspace administration
# ===========================================================================

def can_manage_workspace_settings(role: WorkspaceRole) -> bool:
    """Whether the role may change workspace name, locale, or branding."""
    return is_at_least(role, WorkspaceRole.ADMIN)


def can_manage_workspace_members(role: WorkspaceRole) -> bool:
    """Whether the role may grant or revoke workspace access."""
    return is_at_least(role, WorkspaceRole.ADMIN)


def can_invite_to_workspace(role: WorkspaceRole) -> bool:
    """
    Whether the role may invite a user into this workspace.

    ADMIN only for now. ARCH-06 introduces a per-workspace toggle allowing
    contributors to invite, mirroring the equivalent setting in Slack, Notion,
    and Linear.
    """
    return is_at_least(role, WorkspaceRole.ADMIN)


def can_assign_workspace_role(
    organization_role: OrganizationRole | None,
    effective_workspace_role: WorkspaceRole | None,
    target_role: WorkspaceRole,
) -> bool:
    """
    Whether the actor may grant the given workspace role.

    The signature spans both tiers because the decision genuinely does:

      - Workspace ADMIN may grant CONTRIBUTOR and VIEWER.
      - Only organization OWNER or ADMIN may grant workspace ADMIN.

    Restricting ADMIN grants to the organization tier resolves a deadlock that
    a single-tier rule cannot. If workspace administrators could neither create
    nor modify a peer, two ADMINs in one workspace would be mutually
    unmanageable with no higher workspace role available to break the tie.
    Holding that authority one level up mirrors GitHub, where repository admins
    manage collaborators and organization owners manage repository admins.

    Args:
        organization_role: The actor's organization role, or None.
        effective_workspace_role: The actor's resolved workspace role, from
            resolve_effective_workspace_role.
        target_role: The role being granted.

    Returns:
        True if the assignment is permitted.
    """
    if effective_workspace_role is None:
        return False
    if not can_manage_workspace_members(effective_workspace_role):
        return False

    if target_role is WorkspaceRole.ADMIN:
        return (
            organization_role is not None
            and organization_role in IMPLICIT_WORKSPACE_ADMIN_ROLES
        )

    return target_role in WORKSPACE_ADMIN_ASSIGNABLE_ROLES


def can_modify_workspace_member(
    organization_role: OrganizationRole | None,
    effective_workspace_role: WorkspaceRole | None,
    target_role: WorkspaceRole,
) -> bool:
    """
    Whether the actor may modify or revoke an existing workspace grant.

    Symmetric with can_assign_workspace_role: revoking a workspace ADMIN
    requires organization-level standing, while CONTRIBUTOR and VIEWER grants
    are administrable from within the workspace.

    Self-directed actions (leaving a workspace) are not covered here and are
    governed by the service layer.
    """
    if effective_workspace_role is None:
        return False
    if not can_manage_workspace_members(effective_workspace_role):
        return False

    if target_role is WorkspaceRole.ADMIN:
        return (
            organization_role is not None
            and organization_role in IMPLICIT_WORKSPACE_ADMIN_ROLES
        )

    return True