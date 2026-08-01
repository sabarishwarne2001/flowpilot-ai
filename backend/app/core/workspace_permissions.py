from __future__ import annotations

from app.models.workspace import WorkspaceRole

# Numeric hierarchy mapping for role comparison
ROLE_HIERARCHY = {
    WorkspaceRole.OWNER: 4,
    WorkspaceRole.MANAGER: 3,
    WorkspaceRole.CONTRIBUTOR: 2,
    WorkspaceRole.VIEWER: 1,
}


def _get_role_weight(role: WorkspaceRole | str) -> int:
    """
    Translates a role enum or string into its hierarchy weight.
    Unknown roles default to weight 0.
    """
    try:
        role_enum = WorkspaceRole(role) if isinstance(role, str) else role
        return ROLE_HIERARCHY.get(role_enum, 0)
    except ValueError:
        return 0


def is_at_least(role: WorkspaceRole | str, minimum_role: WorkspaceRole) -> bool:
    """
    Stateless check to verify if a user's role meets or exceeds a target minimum role.
    """
    return _get_role_weight(role) >= _get_role_weight(minimum_role)


def is_workspace_owner(role: WorkspaceRole | str) -> bool:
    """
    Verifies if the user is a Workspace Owner.
    """
    return is_at_least(role, WorkspaceRole.OWNER)


def is_workspace_manager(role: WorkspaceRole | str) -> bool:
    """
    Verifies if the user has at least Manager permissions.
    """
    return is_at_least(role, WorkspaceRole.MANAGER)


def is_workspace_contributor(role: WorkspaceRole | str) -> bool:
    """
    Verifies if the user has at least Contributor permissions.
    """
    return is_at_least(role, WorkspaceRole.CONTRIBUTOR)


def is_workspace_viewer(role: WorkspaceRole | str) -> bool:
    """
    Verifies if the user has at least Viewer permissions.
    """
    return is_at_least(role, WorkspaceRole.VIEWER)


def can_manage_members(role: WorkspaceRole | str) -> bool:
    """
    Determines if a user role is permitted to perform member management actions.
    Typically reserved for OWNER and MANAGER roles.
    """
    return is_at_least(role, WorkspaceRole.MANAGER)


def can_invite_members(role: WorkspaceRole | str) -> bool:
    """
    Determines if a user role is permitted to invite new members.
    Typically reserved for OWNER and MANAGER roles.
    """
    return is_at_least(role, WorkspaceRole.MANAGER)


def can_remove_members(role: WorkspaceRole | str) -> bool:
    """
    Determines if a user role is permitted to remove workspace members.
    Typically reserved for OWNER and MANAGER roles.
    """
    return is_at_least(role, WorkspaceRole.MANAGER)


def can_change_member_roles(role: WorkspaceRole | str) -> bool:
    """
    Determines if a user role is permitted to modify membership roles.
    Typically reserved for OWNER and MANAGER roles.
    """
    return is_at_least(role, WorkspaceRole.MANAGER)


def can_delete_workspace(role: WorkspaceRole | str) -> bool:
    """
    Determines if a user role is permitted to completely delete a workspace.
    Strictly restricted to the OWNER role.
    """
    return is_at_least(role, WorkspaceRole.OWNER)


def can_modify_member_role(actor_role: WorkspaceRole | str, target_role: WorkspaceRole | str) -> bool:
    """
    Verifies if an actor possesses sufficient hierarchical authority to alter or
    remove a target member's record. An actor cannot modify an account of equal 
    or greater weight (e.g. a MANAGER cannot alter an OWNER or another MANAGER).
    """
    actor_weight = _get_role_weight(actor_role)
    target_weight = _get_role_weight(target_role)

    # Actor must hold at least a MANAGER position, and have strictly higher weight than the target
    return actor_weight >= ROLE_HIERARCHY[WorkspaceRole.MANAGER] and actor_weight > target_weight