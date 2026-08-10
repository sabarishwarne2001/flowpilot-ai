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
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.crud.membership_filters import ACTIVE_ONLY, SEAT_CONSUMING_STATUSES
from app.models.organization import (
    MembershipStatus,
    OrganizationMember,
    OrganizationRole,
)


# ============================================================================
# Creation
# ============================================================================

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


# ============================================================================
# Retrieval
# ============================================================================

def get_organization_member(
    db: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    statuses: Sequence[MembershipStatus] | None = None,
) -> OrganizationMember | None:
    """
    Fetches a user's membership in a specific organization.
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
    Counts seats currently consumed by organization member rows.

    NOT the whole seat figure. ARCH-04 invitations create no OrganizationMember
    row — organization_members.user_id is NOT NULL and an invitee may have no
    account — so outstanding invitations are invisible here. MembershipStatus
    .INVITED remains in SEAT_CONSUMING_STATUSES but nothing in ARCH-04 writes
    it.

    The complete figure is this count plus
    organization_invitation.count_pending_invitations, combined in exactly one
    place: organization_invitation_service.count_reserved_seats. Enforce a seat
    ceiling on that, never on this.
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


# ============================================================================
# Mutation
# ============================================================================

def update_organization_member_role(
    db: Session,
    *,
    membership: OrganizationMember,
    role: OrganizationRole,
) -> OrganizationMember:
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
    membership.status = MembershipStatus.DEACTIVATED
    membership.deactivated_at = datetime.now(timezone.utc)
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
    membership.status = MembershipStatus.ACTIVE
    membership.deactivated_at = None
    membership.deactivated_by_id = None
    if role is not None:
        membership.role = role
    db.add(membership)
    db.flush()
    return membership