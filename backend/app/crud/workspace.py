"""
Persistence operations for the Workspace collaboration boundary.

Every workspace belongs to exactly one organization, and every query here names
its tenant explicitly.

Layering: queries and flushes only. No authorization, no commits.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.crud.membership_filters import ACTIVE_ONLY
from app.models.organization import MembershipStatus
from app.models.uploaded_file import UploadedFile
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
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE,
) -> Workspace:
    """
    Inserts a workspace under an organization and flushes it.
    """
    workspace = Workspace(
        organization_id=organization_id,
        slug=slug,
        workspace_name=workspace_name,
        timezone=timezone,
        language=language,
        currency=currency,
        date_format=date_format,
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
) -> Workspace:
    """
    Applies a partial update to a workspace.
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

    db.add(workspace)
    db.flush()
    return workspace


def clear_workspace_logo(db: Session, *, workspace: Workspace) -> Workspace:
    """
    Removes the workspace logo: soft-deletes the uploaded_files row and drops
    the pointer (logo_file_id).

    ARCH-08 Step 1 (Regression R-B Fix): previously cleared only the legacy
    company_logo_url column.
    """
    if workspace.logo_file_id is not None:
        record = db.get(UploadedFile, workspace.logo_file_id)
        if record is not None and record.deleted_at is None:
            record.deleted_at = datetime.now(UTC)
    workspace.logo_file_id = None
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
    """
    workspace.status = status
    db.add(workspace)
    db.flush()
    return workspace