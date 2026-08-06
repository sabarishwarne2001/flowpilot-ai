"""
Persistence operations for the Workspace collaboration boundary.

Every workspace belongs to exactly one organization, and every query here names
its tenant explicitly.

Two functions were removed in ARCH-01 and have no replacement, because both
answered questions that are incoherent under a tenant model:

  get_workspace(db, user_id)
      Resolved "the user's workspace" via scalar_one_or_none on a query that
      could legitimately match many rows, raising MultipleResultsFound and
      permanently breaking any account holding a second membership. Callers now
      name the workspace they mean, and the request context resolves it.

  get_first_workspace(db)
      Returned the oldest workspace row in the database and backed an
      unauthenticated public endpoint, disclosing one tenant's branding to
      every visitor. Branding now resolves from an invitation token or an
      authorized context.

Layering: queries and flushes only. No authorization, no commits.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.crud.membership_filters import ACTIVE_ONLY
from app.models.organization import MembershipStatus
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceStatus


# ===========================================================================
# Creation
# ===========================================================================

def create_workspace(
    db: Session,
    *,
    organization_id: uuid.UUID,
    slug: str,
    workspace_name: str,
    timezone: str = "UTC",
    language: str = "en",
    currency: str = "USD",
    date_format: str = "YYYY-MM-DD",
    company_logo_url: str | None = None,
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE,
) -> Workspace:
    """
    Inserts a workspace under an organization and flushes it.

    Locale and branding live on the workspace rather than the organization
    because a US and an India workspace on one contract legitimately need
    different currency, timezone, and date formatting.

    Slug uniqueness is scoped to the organization by
    uq_workspace_organization_slug, so two tenants may both have a workspace
    called "engineering".
    """
    workspace = Workspace(
        organization_id=organization_id,
        slug=slug,
        workspace_name=workspace_name,
        timezone=timezone,
        language=language,
        currency=currency,
        date_format=date_format,
        company_logo_url=company_logo_url,
        status=status,
    )
    db.add(workspace)
    db.flush()
    return workspace


# ===========================================================================
# Retrieval
# ===========================================================================

def get_workspace_by_id(
    db: Session,
    *,
    workspace_id: uuid.UUID,
) -> Workspace | None:
    """
    Fetches a workspace by primary key, regardless of status.

    Deliberately unscoped by organization: the workspace identifier already
    determines its organization, so requiring both would create two sources of
    truth and an inconsistency check on every request. The request context
    resolves the organization from the returned row.
    """
    stmt = select(Workspace).where(Workspace.id == workspace_id)
    return db.execute(stmt).scalar_one_or_none()


def get_workspace_with_organization(
    db: Session,
    *,
    workspace_id: uuid.UUID,
) -> Workspace | None:
    """
    Fetches a workspace with its parent organization eagerly loaded.

    Used by the request context dependency, which needs both objects on every
    tenant-scoped request. Eager loading here turns two round trips into one on
    the hottest path in the application.
    """
    stmt = (
        select(Workspace)
        .options(joinedload(Workspace.organization))
        .where(Workspace.id == workspace_id)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_workspace_by_slug(
    db: Session,
    *,
    organization_id: uuid.UUID,
    slug: str,
) -> Workspace | None:
    """
    Resolves a workspace from its organization-scoped slug.

    Backs the /{organization}/{workspace}/... URL shape. Safe as a scalar:
    uq_workspace_organization_slug guarantees at most one match.
    """
    stmt = select(Workspace).where(
        Workspace.organization_id == organization_id,
        Workspace.slug == slug,
    )
    return db.execute(stmt).scalar_one_or_none()


def is_workspace_slug_available(
    db: Session,
    *,
    organization_id: uuid.UUID,
    slug: str,
) -> bool:
    """
    Whether the slug is unclaimed within the organization.

    Advisory only; the unique constraint remains the authority.
    """
    stmt = (
        select(Workspace.id)
        .where(
            Workspace.organization_id == organization_id,
            Workspace.slug == slug,
        )
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none() is None


def list_workspaces_for_organization(
    db: Session,
    *,
    organization_id: uuid.UUID,
    statuses: Sequence[WorkspaceStatus] | None = None,
) -> list[Workspace]:
    """
    Returns every workspace belonging to an organization.

    This is the set visible to an organization OWNER or ADMIN, who hold a
    derived ADMIN grant everywhere. Applying that derivation is the service
    layer's job; this function does not decide who may call it.
    """
    stmt = select(Workspace).where(
        Workspace.organization_id == organization_id
    )
    if statuses is not None:
        stmt = stmt.where(Workspace.status.in_(statuses))

    stmt = stmt.order_by(Workspace.workspace_name.asc(), Workspace.id.asc())
    return list(db.execute(stmt).scalars().all())


def list_granted_workspaces_for_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID | None = None,
    statuses: Sequence[MembershipStatus] | None = ACTIVE_ONLY,
) -> list[Workspace]:
    """
    Returns workspaces where the user holds an explicit grant.

    Deliberately does NOT include workspaces visible only through an
    organization-level derived grant. Composing the two sets requires the
    elevation rule from app.core.workspace_permissions, which is policy and
    belongs to the service layer. A CRUD function that applied it silently
    would hide an authorization decision inside a query.

    Args:
        organization_id: Optionally restricts results to one organization.
    """
    stmt = (
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user_id)
    )
    if statuses is not None:
        stmt = stmt.where(WorkspaceMember.status.in_(statuses))
    if organization_id is not None:
        stmt = stmt.where(Workspace.organization_id == organization_id)

    stmt = stmt.order_by(Workspace.workspace_name.asc(), Workspace.id.asc())
    return list(db.execute(stmt).scalars().all())


def count_workspaces_for_organization(
    db: Session,
    *,
    organization_id: uuid.UUID,
    statuses: Sequence[WorkspaceStatus] | None = None,
) -> int:
    """
    Counts workspaces in an organization, for plan limit enforcement.
    """
    stmt = (
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.organization_id == organization_id)
    )
    if statuses is not None:
        stmt = stmt.where(Workspace.status.in_(statuses))
    return db.execute(stmt).scalar_one()


# ===========================================================================
# Mutation
# ===========================================================================

def update_workspace(
    db: Session,
    *,
    workspace: Workspace,
    workspace_name: str | None = None,
    slug: str | None = None,
    timezone: str | None = None,
    language: str | None = None,
    currency: str | None = None,
    date_format: str | None = None,
    company_logo_url: str | None = None,
) -> Workspace:
    """
    Applies a partial update to a workspace.

    None means "leave unchanged", matching PATCH semantics. Clearing the logo
    is therefore handled by clear_workspace_logo rather than by passing None.
    """
    if workspace_name is not None:
        workspace.workspace_name = workspace_name
    if slug is not None:
        workspace.slug = slug
    if timezone is not None:
        workspace.timezone = timezone
    if language is not None:
        workspace.language = language
    if currency is not None:
        workspace.currency = currency
    if date_format is not None:
        workspace.date_format = date_format
    if company_logo_url is not None:
        workspace.company_logo_url = company_logo_url

    db.add(workspace)
    db.flush()
    return workspace


def clear_workspace_logo(db: Session, *, workspace: Workspace) -> Workspace:
    """
    Removes the workspace logo reference.

    Explicit function rather than a None argument to update_workspace, which
    reserves None for "unchanged". Deleting the underlying file is the service
    layer's responsibility.
    """
    workspace.company_logo_url = None
    db.add(workspace)
    db.flush()
    return workspace


def set_workspace_status(
    db: Session,
    *,
    workspace: Workspace,
    status: WorkspaceStatus,
) -> Workspace:
    """
    Sets the lifecycle status of a workspace.

    ARCHIVED is a soft delete, retained and restorable within the
    organization's retention window. SUSPENDED is an administrative or billing
    block. No CRUD function deletes a workspace row.
    """
    workspace.status = status
    db.add(workspace)
    db.flush()
    return workspace