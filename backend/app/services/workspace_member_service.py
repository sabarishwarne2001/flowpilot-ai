"""
Business orchestration for workspace membership — the access grant.

Distinct from organization membership, which is the billable seat. This module
enforces invariant B.1 #2: a workspace grant may only exist where an ACTIVE
organization membership exists for the same user and tenant.

ARCH-07 Step 3: Converted AUDIT log call sites to structured audit_service.record().
Preserves all original WorkspaceAccess dataclass, resolution, and grant mechanics.
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
from app.models.audit_log import AuditAction, AuditResourceType
from app.services import audit_service

logger = logging.getLogger("app.services.workspace_member_service")


@dataclass(frozen=True)
class WorkspaceAccess:
    """
    An actor's fully resolved standing in one workspace.
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
    organization_membership = (
        organization_members_crud.get_organization_member(
            db,
            organization_id=workspace.organization_id,
            user_id=user_id,
            statuses=ACTIVE_ONLY,
        )
    )

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
            action = AuditAction.ENABLED
        else:
            membership = workspace_members_crud.create_workspace_member(
                db,
                workspace_id=workspace.id,
                user_id=target_user_id,
                role=role,
                status=MembershipStatus.ACTIVE,
            )
            action = AuditAction.CREATED

        audit_service.record(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            actor_id=actor_access.actor_user_id,
            resource_type=AuditResourceType.MEMBERSHIP,
            resource_id=membership.id,
            action=action,
            details={
                "scope": "WORKSPACE_GRANT",
                "target_user_id": str(target_user_id),
                "role": role.value,
                "workspace_slug": workspace.slug,
            },
        )

        commit_and_refresh(db, membership)
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

        audit_service.record(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            actor_id=actor_access.actor_user_id,
            resource_type=AuditResourceType.MEMBERSHIP,
            resource_id=target_membership.id,
            action=AuditAction.ROLE_CHANGED,
            details={
                "scope": "WORKSPACE_GRANT",
                "target_user_id": str(target_membership.user_id),
                "old_role": previous_role.value,
                "new_role": new_role.value,
                "workspace_slug": workspace.slug,
            },
        )

        commit_and_refresh(db, updated)
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

        audit_service.record(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            actor_id=actor_user_id,
            resource_type=AuditResourceType.MEMBERSHIP,
            resource_id=target_membership.id,
            action=AuditAction.DISABLED,
            details={
                "scope": "WORKSPACE_GRANT",
                "target_user_id": str(target_membership.user_id),
                "workspace_slug": workspace.slug,
            },
        )

        commit_and_refresh(db, revoked)
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
    if access.workspace_membership is None:
        raise WorkspaceAccessDeniedError("Workspace not found.")

    try:
        left = workspace_members_crud.deactivate_workspace_member(
            db,
            membership=access.workspace_membership,
            actor_id=access.workspace_membership.user_id,
        )

        audit_service.record(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            actor_id=left.user_id,
            resource_type=AuditResourceType.MEMBERSHIP,
            resource_id=left.id,
            action=AuditAction.DISABLED,
            details={
                "scope": "WORKSPACE_GRANT",
                "target_user_id": str(left.user_id),
                "workspace_slug": workspace.slug,
                "self_initiated": True,
            },
        )

        commit_and_refresh(db, left)
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
