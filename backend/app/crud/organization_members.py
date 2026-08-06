"""
Persistence operations for organization membership — the billable seat.

A user belonging to five workspaces of one organization holds exactly one
OrganizationMember row and therefore consumes one seat. Workspace grants are
tracked separately in app/crud/workspace_members.py.

Layering: queries and flushes only. No authorization, no invariant enforcement,
no commits. "An organization must retain an active owner" is a service-layer
rule because it requires acting on a count; this module supplies
count_active_owners so the service can enforce it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.crud.membership_filters import ACTIVE_ONLY, SEAT_CONSUMING_STATUSES
from app.models.organization import (
    MembershipStatus,
    OrganizationMember,
    OrganizationRole,
)


# ===========================================================================
# Creation
# ===========================================================================

def create_organization_member(
    db: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    role: OrganizationRole = OrganizationRole.MEMBER,
    status: MembershipStatus = MembershipStatus.ACTIVE,
) -> OrganizationMember:
    """
    Creates a seat for a user in an organization.

    Constrained by uq_organization_user_membership, so re-adding a former
    member must go through reactivate_organization_member rather than this
    function.
    """
    membership = OrganizationMember(
        organization_id=organization_id,
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

def get_organization_member(
    db: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    statuses: Sequence[MembershipStatus] | None = None,
) -> OrganizationMember | None:
    """
    Fetches a user's membership in a specific organization.

    Safe as a scalar: uq_organization_user_membership guarantees at most one
    row for the pair. Contrast the pre-ARCH-01 query, which used the same
    accessor on a genuinely multi-row result and crashed on the second
    membership.

    Args:
        statuses: Restricts to these statuses. Pass ACTIVE_ONLY for
            authorization paths; leave None for administrative views that must
            see deactivated records.
    """
    stmt = select(OrganizationMember).where(
        OrganizationMember.organization_id == organization_id,
        OrganizationMember.user_id == user_id,
    )
    if statuses is not None:
        stmt = stmt.where(OrganizationMember.status.in_(statuses))
    return db.execute(stmt).scalar_one_or_none()


def get_organization_member_by_id(
    db: Session,
    *,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> OrganizationMember | None:
    """
    Fetches a membership by its own identifier, scoped to an organization.

    The organization_id filter is not redundant. Without it, an actor
    authorized for organization A could address a membership belonging to
    organization B by supplying its identifier. Scoping every by-id lookup to
    its tenant is what keeps that class of bug out of the codebase.
    """
    stmt = select(OrganizationMember).where(
        OrganizationMember.id == membership_id,
        OrganizationMember.organization_id == organization_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_organization_members(
    db: Session,
    *,
    organization_id: uuid.UUID,
    statuses: Sequence[MembershipStatus] | None = None,
) -> list[OrganizationMember]:
    """
    Returns the member directory for an organization.

    Ordered by role then creation time, with the membership identifier as a
    unique tiebreaker for stable pagination.
    """
    stmt = select(OrganizationMember).where(
        OrganizationMember.organization_id == organization_id
    )
    if statuses is not None:
        stmt = stmt.where(OrganizationMember.status.in_(statuses))

    stmt = stmt.order_by(
        OrganizationMember.role.asc(),
        OrganizationMember.created_at.asc(),
        OrganizationMember.id.asc(),
    )
    return list(db.execute(stmt).scalars().all())


def list_memberships_for_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    statuses: Sequence[MembershipStatus] | None = ACTIVE_ONLY,
) -> list[OrganizationMember]:
    """
    Returns every organization membership held by a user.

    Backs the bootstrap context endpoint, which must report all tenants a user
    belongs to in a single round trip.
    """
    stmt = select(OrganizationMember).where(
        OrganizationMember.user_id == user_id
    )
    if statuses is not None:
        stmt = stmt.where(OrganizationMember.status.in_(statuses))

    stmt = stmt.order_by(
        OrganizationMember.created_at.asc(),
        OrganizationMember.id.asc(),
    )
    return list(db.execute(stmt).scalars().all())


def count_active_owners(db: Session, *, organization_id: uuid.UUID) -> int:
    """
    Counts active owners of an organization.

    Supplies the service layer with what it needs to enforce the invariant that
    an organization must always retain at least one active owner. Without it,
    the last owner could leave and strand the tenant with nobody able to manage
    billing or transfer ownership.
    """
    stmt = (
        select(func.count())
        .select_from(OrganizationMember)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role == OrganizationRole.OWNER,
            OrganizationMember.status == MembershipStatus.ACTIVE,
        )
    )
    return db.execute(stmt).scalar_one()


def count_consumed_seats(db: Session, *, organization_id: uuid.UUID) -> int:
    """
    Counts seats currently consumed by an organization.

    Includes pending invitations so that a tenant cannot exceed its plan by
    issuing invitations it has no seats for. Consumed by ARCH-05.
    """
    stmt = (
        select(func.count())
        .select_from(OrganizationMember)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.status.in_(SEAT_CONSUMING_STATUSES),
        )
    )
    return db.execute(stmt).scalar_one()


# ===========================================================================
# Mutation
# ===========================================================================

def update_organization_member_role(
    db: Session,
    *,
    membership: OrganizationMember,
    role: OrganizationRole,
) -> OrganizationMember:
    """
    Changes a member's organization role.

    Authorization is the caller's responsibility, via
    app.core.organization_permissions.can_modify_member_role. This function is
    the mechanism the pre-ARCH-01 codebase never had at all, which is why
    promotion, demotion, and ownership transfer were impossible.
    """
    membership.role = role
    db.add(membership)
    db.flush()
    return membership


def set_organization_member_status(
    db: Session,
    *,
    membership: OrganizationMember,
    status: MembershipStatus,
) -> OrganizationMember:
    """
    Sets a membership status directly.

    Used for suspension and for accepting an invitation (INVITED -> ACTIVE).
    Use deactivate_organization_member for removal, so that the actor and
    timestamp are recorded.
    """
    membership.status = status
    db.add(membership)
    db.flush()
    return membership


def deactivate_organization_member(
    db: Session,
    *,
    membership: OrganizationMember,
    actor_id: uuid.UUID | None,
) -> OrganizationMember:
    """
    Deactivates a membership, retaining the row.

    Replaces the hard DELETE used before ARCH-01. The row survives so that
    attribution for past work is preserved, the audit log has a stable subject,
    and a later re-add is traceable. Slack calls this a deactivated member;
    GitHub retains the organization audit entry permanently.

    Args:
        actor_id: The administrator performing the removal, or None for
            system-initiated deactivation.
    """
    membership.status = MembershipStatus.DEACTIVATED
    membership.deactivated_at = datetime.now(UTC)
    membership.deactivated_by_id = actor_id
    db.add(membership)
    db.flush()
    return membership


def reactivate_organization_member(
    db: Session,
    *,
    membership: OrganizationMember,
    role: OrganizationRole | None = None,
) -> OrganizationMember:
    """
    Restores a deactivated or suspended membership.

    Clears the deactivation record, so the row reflects current state while the
    audit log retains the history of what happened and when.
    """
    membership.status = MembershipStatus.ACTIVE
    membership.deactivated_at = None
    membership.deactivated_by_id = None
    if role is not None:
        membership.role = role
    db.add(membership)
    db.flush()
    return membership