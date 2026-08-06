"""
Business orchestration for workspace membership — the access grant.

Distinct from organization membership, which is the billable seat. This module
enforces invariant B.1 #2: a workspace grant may only exist where an ACTIVE
organization membership exists for the same user and tenant. That constraint
cannot be expressed in the database without denormalizing organization_id onto
workspace_members, so it lives here and is asserted by the isolation tests.

resolve_workspace_access is the composition point for the ARCH-01 permission
model. It joins both membership lookups with the derivation rule from
app.core.workspace_permissions, and Step 9a's request context calls it on every
tenant-scoped request.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.exceptions import (
    WorkspaceAccessDeniedError,
    WorkspaceMemberError,
    WorkspacePermissionDeniedError,
)
from app.core.transactions import commit_and_refresh, rollback_and_log_error
from app.core.workspace_permissions import (
    can_assign_workspace_role,
    can_modify_workspace_member,
    resolve_effective_workspace_role,
)
from app.crud import organization_members as organization_members_crud
from app.crud import workspace_members as workspace_members_crud
from app.crud.membership_filters import ACTIVE_ONLY
from app.models.organization import (
    MembershipStatus,
    OrganizationMember,
    OrganizationRole,
)
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole

logger = logging.getLogger("app.services.workspace_member_service")


@dataclass(frozen=True)
class WorkspaceAccess:
    """
    An actor's fully resolved standing in one workspace.

    effective_role is None when the actor has no access at all, which the API
    layer converts to 404 rather than 403 so the response cannot confirm that a
    workspace exists.

    organization_membership is carried alongside because several downstream
    decisions — granting workspace ADMIN, for instance — legitimately span both
    tiers and would otherwise require a second lookup.
    """
    organization_membership: OrganizationMember | None
    workspace_membership: WorkspaceMember | None
    effective_role: WorkspaceRole | None

    @property
    def organization_role(self) -> OrganizationRole | None:
        if self.organization_membership is None:
            return None
        return self.organization_membership.role

    @property
    def actor_user_id(self) -> uuid.UUID | None:
        if self.organization_membership is None:
            return None
        return self.organization_membership.user_id

    @property
    def has_access(self) -> bool:
        return self.effective_role is not None


def resolve_workspace_access(
    db: Session,
    *,
    workspace: Workspace,
    user_id: uuid.UUID,
) -> WorkspaceAccess:
    """
    Resolves an actor's effective standing in a workspace.

    The single authority on workspace access, called on every tenant-scoped
    request. Organization OWNER and ADMIN resolve to workspace ADMIN whether or
    not an explicit grant exists; that elevation is derived here and never
    persisted, so an organization role change takes effect on the very next
    request instead of leaving stale grants behind.

    Costs two indexed lookups, with the second skipped for organization
    administrators. ARCH-11 introduces caching with invalidation on role change.
    """
    organization_membership = (
        organization_members_crud.get_organization_member(
            db,
            organization_id=workspace.organization_id,
            user_id=user_id,
            statuses=ACTIVE_ONLY,
        )
    )

    # Organization membership is a precondition for any workspace access. A
    # grant without one is an invariant violation, not an access path.
    if organization_membership is None:
        return WorkspaceAccess(None, None, None)

    workspace_membership = workspace_members_crud.get_workspace_member(
        db,
        workspace_id=workspace.id,
        user_id=user_id,
        statuses=ACTIVE_ONLY,
    )

    effective_role = resolve_effective_workspace_role(
        organization_membership.role,
        workspace_membership.role if workspace_membership else None,
    )

    return WorkspaceAccess(
        organization_membership=organization_membership,
        workspace_membership=workspace_membership,
        effective_role=effective_role,
    )


def list_workspace_members(
    db: Session,
    *,
    workspace: Workspace,
) -> list[WorkspaceMember]:
    """
    Returns explicit grants on a workspace.

    Organization administrators holding only a derived grant do not appear
    here. The API layer merges them into the presented directory so the UI
    shows everyone with access rather than everyone with a stored row.
    """
    return workspace_members_crud.list_workspace_members(
        db,
        workspace_id=workspace.id,
        statuses=(MembershipStatus.ACTIVE, MembershipStatus.SUSPENDED),
    )


def grant_workspace_access(
    db: Session,
    *,
    workspace: Workspace,
    actor_access: WorkspaceAccess,
    target_user_id: uuid.UUID,
    role: WorkspaceRole,
) -> WorkspaceMember:
    """
    Grants a user access to a workspace at the given role.

    Enforces invariant B.1 #2: the target must already hold an ACTIVE
    organization membership. A user cannot be added to a workspace of an
    organization they do not belong to, because the seat is what authorizes
    their presence in the tenant at all.

    A previously revoked grant is reactivated rather than duplicated. The
    unique constraint would reject a duplicate in any case, and reactivating
    keeps the member's history on a single record.
    """
    if not can_assign_workspace_role(
        actor_access.organization_role, actor_access.effective_role, role
    ):
        raise WorkspacePermissionDeniedError(
            "You do not have permission to grant this workspace role."
        )

    target_seat = organization_members_crud.get_organization_member(
        db,
        organization_id=workspace.organization_id,
        user_id=target_user_id,
        statuses=ACTIVE_ONLY,
    )
    if target_seat is None:
        raise WorkspaceMemberError(
            "This user is not an active member of the organization. Invite "
            "them to the organization before granting workspace access."
        )

    existing = workspace_members_crud.get_workspace_member(
        db, workspace_id=workspace.id, user_id=target_user_id, statuses=None
    )

    try:
        if existing is not None:
            membership = workspace_members_crud.reactivate_workspace_member(
                db, membership=existing, role=role
            )
            action = "WORKSPACE_ACCESS_RESTORED"
        else:
            membership = workspace_members_crud.create_workspace_member(
                db,
                workspace_id=workspace.id,
                user_id=target_user_id,
                role=role,
                status=MembershipStatus.ACTIVE,
            )
            action = "WORKSPACE_ACCESS_GRANTED"

        commit_and_refresh(db, membership)

        logger.info(
            "AUDIT | %s | Workspace: %s | User: %s | Role: %s | Actor: %s",
            action,
            workspace.id,
            target_user_id,
            role.value,
            actor_access.actor_user_id,
        )
        return membership

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to grant workspace %s access to user %s: %s",
            workspace.id,
            target_user_id,
            str(exc),
            exc=exc,
        )


def change_workspace_member_role(
    db: Session,
    *,
    workspace: Workspace,
    actor_access: WorkspaceAccess,
    target_membership: WorkspaceMember,
    new_role: WorkspaceRole,
) -> WorkspaceMember:
    """
    Changes an existing workspace grant.

    Both halves are checked: the actor must be permitted to modify the target's
    current role and to assign the new one. Granting or revoking workspace
    ADMIN additionally requires organization-level standing, which resolves the
    deadlock two workspace admins would otherwise create — neither able to
    manage the other, with no higher workspace role to break the tie.
    """
    if not can_modify_workspace_member(
        actor_access.organization_role,
        actor_access.effective_role,
        target_membership.role,
    ):
        raise WorkspacePermissionDeniedError(
            "You do not have permission to modify this member's access."
        )

    if not can_assign_workspace_role(
        actor_access.organization_role, actor_access.effective_role, new_role
    ):
        raise WorkspacePermissionDeniedError(
            "You do not have permission to assign this workspace role."
        )

    if target_membership.role is new_role:
        return target_membership

    previous_role = target_membership.role

    try:
        updated = workspace_members_crud.update_workspace_member_role(
            db, membership=target_membership, role=new_role
        )
        commit_and_refresh(db, updated)

        logger.info(
            "AUDIT | WORKSPACE_ROLE_CHANGED | Workspace: %s | User: %s | "
            "%s -> %s | Actor: %s",
            workspace.id,
            target_membership.user_id,
            previous_role.value,
            new_role.value,
            actor_access.actor_user_id,
        )
        return updated

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to change workspace role for member %s: %s",
            target_membership.id,
            str(exc),
            exc=exc,
        )


def revoke_workspace_access(
    db: Session,
    *,
    workspace: Workspace,
    actor_access: WorkspaceAccess,
    target_membership: WorkspaceMember,
) -> WorkspaceMember:
    """
    Revokes a workspace grant, retaining the row.

    The organization seat is untouched: losing access to one workspace is not
    the same as leaving the company, and conflating them would make a routine
    reassignment look like a termination.

    Unlike an organization, a workspace cannot be orphaned by this operation.
    Every organization OWNER and ADMIN retains a derived ADMIN grant, so no
    last-admin guard is needed here.
    """
    if not can_modify_workspace_member(
        actor_access.organization_role,
        actor_access.effective_role,
        target_membership.role,
    ):
        raise WorkspacePermissionDeniedError(
            "You do not have permission to revoke this member's access."
        )

    actor_user_id = actor_access.actor_user_id
    if actor_user_id == target_membership.user_id:
        raise WorkspaceMemberError(
            "Use the leave-workspace operation to remove your own access."
        )

    try:
        revoked = workspace_members_crud.deactivate_workspace_member(
            db, membership=target_membership, actor_id=actor_user_id
        )
        commit_and_refresh(db, revoked)

        logger.info(
            "AUDIT | WORKSPACE_ACCESS_REVOKED | Workspace: %s | User: %s | "
            "Actor: %s",
            workspace.id,
            target_membership.user_id,
            actor_user_id,
        )
        return revoked

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to revoke workspace access for member %s: %s",
            target_membership.id,
            str(exc),
            exc=exc,
        )


def leave_workspace(
    db: Session,
    *,
    workspace: Workspace,
    access: WorkspaceAccess,
) -> WorkspaceMember:
    """
    Removes the acting user's own workspace grant.

    An organization administrator retains derived access afterward, so this
    removes them from the member list without actually cutting them off. That
    is the correct behavior and matches GitHub, where an organization owner
    cannot lock themselves out of a repository they administer.
    """
    if access.workspace_membership is None:
        raise WorkspaceAccessDeniedError("Workspace not found.")

    try:
        left = workspace_members_crud.deactivate_workspace_member(
            db,
            membership=access.workspace_membership,
            actor_id=access.workspace_membership.user_id,
        )
        commit_and_refresh(db, left)

        logger.info(
            "AUDIT | WORKSPACE_LEFT | Workspace: %s | User: %s",
            workspace.id,
            left.user_id,
        )
        return left

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to leave workspace %s: %s",
            workspace.id,
            str(exc),
            exc=exc,
        )