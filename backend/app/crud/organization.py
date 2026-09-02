"""
Persistence operations for the Organization tenant root.

Layering: this module contains queries and flushes only. It holds no business
rules, performs no authorization, and never commits. Transaction boundaries
belong to the service layer, which uses app.core.transactions.

Every function names its tenant explicitly. No query resolves an organization
by inference from a session or a user, because a user may belong to many.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.crud.membership_filters import ACTIVE_ONLY
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)


# ===========================================================================
# Creation
# ===========================================================================

def create_organization(
    db: Session,
    *,
    slug: str,
    name: str,
    legal_name: str | None = None,
    status: OrganizationStatus = OrganizationStatus.ACTIVE,
) -> Organization:
    """
    Inserts a new organization and flushes it into the session.

    The caller is responsible for having allocated a unique slug via
    app.core.slugs.generate_unique_slug. The database enforces uniqueness as a
    backstop, so a race between two concurrent provisioning requests surfaces
    as an IntegrityError for the service layer to handle.
    """
    organization = Organization(
        slug=slug,
        name=name,
        legal_name=legal_name,
        status=status,
    )
    db.add(organization)
    db.flush()
    return organization


# ===========================================================================
# Retrieval
# ===========================================================================

def get_organization_by_id(
    db: Session,
    *,
    organization_id: uuid.UUID,
) -> Organization | None:
    """Fetches an organization by primary key, regardless of status."""
    stmt = select(Organization).where(Organization.id == organization_id)
    return db.execute(stmt).scalar_one_or_none()


def get_organization_by_slug(
    db: Session,
    *,
    slug: str,
) -> Organization | None:
    """
    Fetches an organization by its public slug, regardless of status.

    Safe as a scalar: organizations.slug carries a unique index.
    """
    stmt = select(Organization).where(Organization.slug == slug)
    return db.execute(stmt).scalar_one_or_none()


def is_organization_slug_available(db: Session, *, slug: str) -> bool:
    """
    Whether the slug is unclaimed.

    Advisory only. Two concurrent requests can both observe availability, so
    the unique index remains the authority and the service layer must handle
    the resulting IntegrityError.
    """
    stmt = select(Organization.id).where(Organization.slug == slug).limit(1)
    return db.execute(stmt).scalar_one_or_none() is None


def list_organizations_for_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    statuses: Sequence[MembershipStatus] | None = ACTIVE_ONLY,
) -> list[Organization]:
    """
    Returns every organization in which the user holds a membership.

    Backs the tenant picker and the bootstrap context endpoint. This query
    returning multiple rows is the normal case under ARCH-01, and is precisely
    what the pre-transformation design could not express.

    Args:
        statuses: Membership statuses to include. None includes every status,
            which is useful for administrative views.
    """
    stmt = (
        select(Organization)
        .join(
            OrganizationMember,
            OrganizationMember.organization_id == Organization.id,
        )
        .where(OrganizationMember.user_id == user_id)
    )
    if statuses is not None:
        stmt = stmt.where(OrganizationMember.status.in_(statuses))

    # Unique tiebreaker: equal names would otherwise order nondeterministically
    # and break pagination.
    stmt = stmt.order_by(Organization.name.asc(), Organization.id.asc())
    return list(db.execute(stmt).scalars().all())


def count_organizations_owned_by_user(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> int:
    """
    Counts organizations in which the user is an active owner.

    Consumed by the service layer to enforce per-account creation limits.
    Founding an organization is an account-level capability rather than a role
    permission, so the constraint on it is a plan limit, not an RBAC check.
    """
    stmt = (
        select(func.count())
        .select_from(OrganizationMember)
        .where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.role == OrganizationRole.OWNER,
            OrganizationMember.status == MembershipStatus.ACTIVE,
        )
    )
    return db.execute(stmt).scalar_one()


# ===========================================================================
# Mutation
# ===========================================================================

def update_organization(
    db: Session,
    *,
    organization: Organization,
    name: str | None = None,
    legal_name: str | None = None,
    slug: str | None = None,
) -> Organization:
    """
    Applies a partial update to an organization.

    None means "leave unchanged" rather than "set to null", matching the PATCH
    semantics of the API layer. Clearing legal_name is therefore not
    expressible here and is handled explicitly by the service when needed.
    """
    if name is not None:
        organization.name = name
    if legal_name is not None:
        organization.legal_name = legal_name
    if slug is not None:
        organization.slug = slug

    db.add(organization)
    db.flush()
    return organization


def set_organization_status(
    db: Session,
    *,
    organization: Organization,
    status: OrganizationStatus,
) -> Organization:
    """
    Sets the lifecycle status of an organization.

    Used for suspension (non-payment, policy enforcement) and archival (soft
    delete). Both are reversible; no CRUD function deletes an organization row,
    so the retention window is always available.
    """
    organization.status = status
    db.add(organization)
    db.flush()
    return organization
