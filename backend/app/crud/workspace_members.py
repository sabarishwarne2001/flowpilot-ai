"""
Persistence operations for workspace membership — the access grant.

Distinct from organization membership, which is the billable seat. A user holds
one OrganizationMember row per organization and one WorkspaceMember row per
workspace they have been granted.

Organization OWNER and ADMIN hold a derived ADMIN grant on every workspace
without a row in this table. That derivation lives in
app.core.workspace_permissions and is never persisted here, so an organization
role change takes effect immediately rather than leaving stale grants behind.

remove_membership() was removed in ARCH-01. It executed a hard DELETE, which
destroyed attribution for past work and left the audit trail with no subject.
Use deactivate_workspace_member instead.

Layering: queries and flushes only. No authorization, no commits.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.crud.membership_filters import ACTIVE_ONLY
from app.models.organization import MembershipStatus
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole


# ===========================================================================
# Creation
# ===========================================================================

def create_workspace_member(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    role: WorkspaceRole = WorkspaceRole.VIEWER,
    status: MembershipStatus = MembershipStatus.ACTIVE,
) -> WorkspaceMember:
    """
    Grants a user access to a workspace at a specific role.

    The caller must ensure the user already holds an ACTIVE organization
    membership. That invariant cannot be expressed as a database constraint
    without denormalizing organization_id onto this table, so it is enforced in
    the service layer and asserted by the isolation test suite.
    """
    membership = WorkspaceMember(
        workspace_id=workspace_id,
        user_id=user_id,
        role=role,
        status=status,
    )
    db.add(membership)
    db.flush()
    return membership


# ===========================================================================
# Retrieval
# ===========================================================================

def get_workspace_member(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    statuses: Sequence[MembershipStatus] | None = None,
) -> WorkspaceMember | None:
    """
    Fetches a user's grant on a specific workspace.

    Safe as a scalar: uq_user_workspace_membership guarantees at most one row.
    Note the workspace_id filter, absent from the pre-ARCH-01 equivalent, which
    is what makes this query single-valued rather than a crash waiting on a
    second membership.

    Returns None when no explicit grant exists. That is not the same as "no
    access" — an organization admin has access without a row here. Resolve both
    through workspace_permissions.resolve_effective_workspace_role.
    """
    stmt = select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user_id,
    )
    if statuses is not None:
        stmt = stmt.where(WorkspaceMember.status.in_(statuses))
    return db.execute(stmt).scalar_one_or_none()


def get_workspace_member_by_id(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> WorkspaceMember | None:
    """
    Fetches a grant by its own identifier, scoped to a workspace.

    The workspace_id filter prevents an actor authorized for one workspace from
    addressing a membership in another by supplying its identifier.
    """
    stmt = select(WorkspaceMember).where(
        WorkspaceMember.id == membership_id,
        WorkspaceMember.workspace_id == workspace_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_workspace_members(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    statuses: Sequence[MembershipStatus] | None = None,
) -> list[WorkspaceMember]:
    """
    Returns explicit grants on a workspace.

    Does not include organization admins holding a derived grant. The service
    layer merges both sets when presenting a member list, so that the UI shows
    everyone with access rather than only those with a stored row.
    """
    stmt = select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id
    )
    if statuses is not None:
        stmt = stmt.where(WorkspaceMember.status.in_(statuses))

    stmt = stmt.order_by(
        WorkspaceMember.role.asc(),
        WorkspaceMember.created_at.asc(),
        WorkspaceMember.id.asc(),
    )
    return list(db.execute(stmt).scalars().all())


def list_memberships_for_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID | None = None,
    statuses: Sequence[MembershipStatus] | None = ACTIVE_ONLY,
) -> list[WorkspaceMember]:
    """
    Returns every workspace grant held by a user.

    Multiple rows are the expected result. The pre-ARCH-01 codebase read this
    same relationship through scalar_one_or_none, which is why a second
    membership permanently broke an account.
    """
    stmt = select(WorkspaceMember).where(WorkspaceMember.user_id == user_id)
    if statuses is not None:
        stmt = stmt.where(WorkspaceMember.status.in_(statuses))
    if organization_id is not None:
        stmt = stmt.join(
            Workspace, Workspace.id == WorkspaceMember.workspace_id
        ).where(Workspace.organization_id == organization_id)

    stmt = stmt.order_by(
        WorkspaceMember.created_at.asc(),
        WorkspaceMember.id.asc(),
    )
    return list(db.execute(stmt).scalars().all())


def count_workspace_members(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    statuses: Sequence[MembershipStatus] | None = ACTIVE_ONLY,
) -> int:
    """Counts explicit grants on a workspace."""
    stmt = (
        select(func.count())
        .select_from(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == workspace_id)
    )
    if statuses is not None:
        stmt = stmt.where(WorkspaceMember.status.in_(statuses))
    return db.execute(stmt).scalar_one()


def count_workspace_admins(db: Session, *, workspace_id: uuid.UUID) -> int:
    """
    Counts active explicit ADMIN grants on a workspace.

    Advisory rather than an invariant. Unlike an organization, a workspace may
    legitimately have zero explicit admins, because every organization OWNER
    and ADMIN retains a derived ADMIN grant and can always administer it. A
    workspace can therefore never become orphaned the way the pre-ARCH-01
    single-owner workspace could.
    """
    stmt = (
        select(func.count())
        .select_from(WorkspaceMember)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.role == WorkspaceRole.ADMIN,
            WorkspaceMember.status == MembershipStatus.ACTIVE,
        )
    )
    return db.execute(stmt).scalar_one()


# ===========================================================================
# Mutation
# ===========================================================================

def update_workspace_member_role(
    db: Session,
    *,
    membership: WorkspaceMember,
    role: WorkspaceRole,
) -> WorkspaceMember:
    """
    Changes a member's workspace role.

    Authorization is the caller's responsibility, via
    workspace_permissions.can_assign_workspace_role, which requires
    organization-level standing to grant or revoke workspace ADMIN.
    """
    membership.role = role
    db.add(membership)
    db.flush()
    return membership


def set_workspace_member_status(
    db: Session,
    *,
    membership: WorkspaceMember,
    status: MembershipStatus,
) -> WorkspaceMember:
    """
    Sets a grant status directly.

    Use deactivate_workspace_member for revocation, so the actor and timestamp
    are recorded.
    """
    membership.status = status
    db.add(membership)
    db.flush()
    return membership


def deactivate_workspace_member(
    db: Session,
    *,
    membership: WorkspaceMember,
    actor_id: uuid.UUID | None,
) -> WorkspaceMember:
    """
    Revokes a workspace grant, retaining the row.

    Replaces the hard DELETE used before ARCH-01. Access is revoked on the
    member's very next request, because authorization resolves membership from
    the database per request rather than trusting a token claim.

    Args:
        actor_id: The administrator revoking access, or None for
            system-initiated revocation such as organization-level removal.
    """
    membership.status = MembershipStatus.DEACTIVATED
    membership.deactivated_at = datetime.now(UTC)
    membership.deactivated_by_id = actor_id
    db.add(membership)
    db.flush()
    return membership


def reactivate_workspace_member(
    db: Session,
    *,
    membership: WorkspaceMember,
    role: WorkspaceRole | None = None,
) -> WorkspaceMember:
    """
    Restores a revoked or suspended workspace grant.

    Used when a former member is re-invited: the existing row is reactivated
    rather than duplicated, which uq_user_workspace_membership would reject
    anyway, and which keeps the member's history on one record.
    """
    membership.status = MembershipStatus.ACTIVE
    membership.deactivated_at = None
    membership.deactivated_by_id = None
    if role is not None:
        membership.role = role
    db.add(membership)
    db.flush()
    return membership


def deactivate_all_workspace_grants_for_user(
    db: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    actor_id: uuid.UUID | None,
) -> int:
    """
    Revokes every workspace grant a user holds within one organization.

    Called when a user is removed from an organization: the seat and all access
    it carried must be released together. Leaving orphaned workspace grants
    behind would mean a removed member retained access to individual
    workspaces, which is the most consequential failure mode in a
    multi-workspace tenant.

    Scoped to a single organization, so removing someone from one tenant never
    touches their access in another.

    Returns:
        The number of grants revoked.
    """
    memberships = list_memberships_for_user(
        db,
        user_id=user_id,
        organization_id=organization_id,
        statuses=None,
    )

    revoked = 0
    for membership in memberships:
        if membership.status is MembershipStatus.DEACTIVATED:
            continue
        deactivate_workspace_member(
            db, membership=membership, actor_id=actor_id
        )
        revoked += 1

    return revoked
