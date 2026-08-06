"""
Business orchestration for Workspaces within FlowPilot AI.

A workspace is the collaboration boundary. It always belongs to an
organization, which owns the commercial relationship. Creating the first
workspace of a tenant is part of organization provisioning and lives in
app.services.organization_service; this module creates additional workspaces
inside an existing organization and manages their settings and lifecycle.

Two functions were removed in ARCH-01:

  create_new_workspace()
      Created a workspace and made the caller its OWNER, with no organization
      above it. Ownership is now an organization-level concept, and tenant
      creation is provision_organization.

  update_existing_workspace()
      Reached through a PUT endpoint that also created workspaces. That
      conflation is why an existing owner revisiting the onboarding screen
      silently overwrote their live settings. Creation and update are now
      distinct operations with distinct authorization.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    OrganizationPermissionDeniedError,
    TenantSuspendedError,
    WorkspaceAlreadyExistsError,
    WorkspaceNotFoundError,
    WorkspacePermissionDeniedError,
)
from app.core.organization_permissions import (
    IMPLICIT_WORKSPACE_ADMIN_ROLES,
    can_create_workspace,
    can_delete_workspace,
)
from app.core.slugs import generate_unique_slug, validate_slug
from app.core.transactions import commit_and_refresh, rollback_and_log_error
from app.core.workspace_permissions import can_manage_workspace_settings
from app.crud import workspace as workspace_crud
from app.crud import workspace_members as workspace_members_crud
from app.crud.membership_filters import ACTIVE_ONLY
from app.models.organization import Organization, OrganizationRole
from app.models.workspace import Workspace, WorkspaceRole, WorkspaceStatus

logger = logging.getLogger("app.services.workspace_service")

#: Maximum active workspaces per organization. A plan limit, replaced by a
#: subscription-derived value in ARCH-05.
MAX_WORKSPACES_PER_ORGANIZATION: int = 10


def get_workspace_or_raise(
    db: Session,
    *,
    workspace_id: uuid.UUID,
) -> Workspace:
    """
    Fetches a workspace with its organization eagerly loaded.

    Does not authorize. The request context resolves access separately and
    converts a non-member into 404 rather than 403.
    """
    workspace = workspace_crud.get_workspace_with_organization(
        db, workspace_id=workspace_id
    )
    if workspace is None:
        raise WorkspaceNotFoundError("Workspace not found.")
    return workspace


def resolve_workspace_by_slug(
    db: Session,
    *,
    organization: Organization,
    slug: str,
) -> Workspace:
    """
    Resolves a workspace from its organization-scoped slug.

    Backs the /{organization}/{workspace}/... URL shape, where slugs are the
    human-readable address and identifiers are the internal contract.
    """
    workspace = workspace_crud.get_workspace_by_slug(
        db, organization_id=organization.id, slug=slug
    )
    if workspace is None:
        raise WorkspaceNotFoundError("Workspace not found.")
    return workspace


def assert_workspace_operational(workspace: Workspace) -> None:
    """
    Rejects requests against an archived or suspended workspace.

    Distinguished from an access failure so the client can render a tombstone
    explaining what happened rather than a generic error.
    """
    if workspace.status is not WorkspaceStatus.ACTIVE:
        raise TenantSuspendedError(
            f"This workspace is {workspace.status.value.lower()} "
            "and cannot be accessed."
        )


def list_accessible_workspaces(
    db: Session,
    *,
    organization: Organization,
    user_id: uuid.UUID,
    organization_role: OrganizationRole,
) -> list[Workspace]:
    """
    Returns the workspaces an actor may enter within one organization.

    Composes two CRUD primitives with the derivation rule:

      - Organization OWNER and ADMIN see every workspace, because they hold a
        derived ADMIN grant on all of them.
      - Everyone else sees only workspaces where they hold an explicit grant.

    The composition lives here rather than in the query layer deliberately. A
    CRUD function that silently applied elevation would hide an authorization
    decision inside a query, which is how tenant leaks get written.
    """
    if organization_role in IMPLICIT_WORKSPACE_ADMIN_ROLES:
        return workspace_crud.list_workspaces_for_organization(
            db,
            organization_id=organization.id,
            statuses=(WorkspaceStatus.ACTIVE,),
        )

    return workspace_crud.list_granted_workspaces_for_user(
        db,
        user_id=user_id,
        organization_id=organization.id,
        statuses=ACTIVE_ONLY,
    )


def create_workspace_in_organization(
    db: Session,
    *,
    organization: Organization,
    actor_id: uuid.UUID,
    actor_organization_role: OrganizationRole,
    workspace_name: str,
    slug: str | None = None,
    timezone: str = "UTC",
    language: str = "en",
    currency: str = "USD",
    date_format: str = "YYYY-MM-DD",
) -> Workspace:
    """
    Creates an additional workspace inside an existing organization.

    Distinct from founding an organization, which is an account-level
    capability. Creating a workspace inside a tenant is an administrative act
    within that tenant, so it is governed by organization role.

    Locale defaults are per-workspace rather than inherited from the
    organization, because a US and an India workspace on one contract
    legitimately need different currency, timezone, and date formatting.
    """
    if not can_create_workspace(actor_organization_role):
        raise OrganizationPermissionDeniedError(
            "You do not have permission to create workspaces in this "
            "organization."
        )

    existing_count = workspace_crud.count_workspaces_for_organization(
        db,
        organization_id=organization.id,
        statuses=(WorkspaceStatus.ACTIVE,),
    )
    if existing_count >= MAX_WORKSPACES_PER_ORGANIZATION:
        raise OrganizationPermissionDeniedError(
            f"This organization has reached its limit of "
            f"{MAX_WORKSPACES_PER_ORGANIZATION} workspaces."
        )

    if slug is not None:
        resolved_slug = validate_slug(slug)
        if not workspace_crud.is_workspace_slug_available(
            db, organization_id=organization.id, slug=resolved_slug
        ):
            raise WorkspaceAlreadyExistsError(
                f"A workspace with the identifier '{resolved_slug}' already "
                "exists in this organization."
            )
    else:
        resolved_slug = generate_unique_slug(
            workspace_name,
            is_available=lambda candidate: (
                workspace_crud.is_workspace_slug_available(
                    db, organization_id=organization.id, slug=candidate
                )
            ),
            fallback_prefix="workspace",
        )

    try:
        workspace = workspace_crud.create_workspace(
            db,
            organization_id=organization.id,
            slug=resolved_slug,
            workspace_name=workspace_name.strip(),
            timezone=timezone,
            language=language,
            currency=currency,
            date_format=date_format,
            status=WorkspaceStatus.ACTIVE,
        )

        # An explicit grant for the creator, even though their organization
        # role already derives one. It makes them visible in the member list
        # and preserves their access if their organization role is later
        # reduced.
        workspace_members_crud.create_workspace_member(
            db,
            workspace_id=workspace.id,
            user_id=actor_id,
            role=WorkspaceRole.ADMIN,
        )

        commit_and_refresh(db, workspace)

        logger.info(
            "AUDIT | WORKSPACE_CREATED | Org: %s | Workspace: %s (%s) | "
            "Actor: %s",
            organization.id,
            workspace.id,
            workspace.slug,
            actor_id,
        )
        return workspace

    except IntegrityError as exc:
        db.rollback()
        raise WorkspaceAlreadyExistsError(
            f"The identifier '{resolved_slug}' was just taken. Please try "
            "again."
        ) from exc

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to create workspace in organization %s: %s",
            organization.id,
            str(exc),
            exc=exc,
        )


def update_workspace_settings(
    db: Session,
    *,
    workspace: Workspace,
    effective_role: WorkspaceRole,
    actor_id: uuid.UUID,
    workspace_name: str | None = None,
    slug: str | None = None,
    timezone: str | None = None,
    language: str | None = None,
    currency: str | None = None,
    date_format: str | None = None,
    company_logo_url: str | None = None,
) -> Workspace:
    """
    Updates workspace name, locale, and branding.

    Authorized by effective workspace role, so an organization administrator
    may edit any workspace through their derived ADMIN grant without holding an
    explicit row.

    None means "leave unchanged", matching PATCH semantics. Removing the logo
    is handled by remove_workspace_logo.
    """
    if not can_manage_workspace_settings(effective_role):
        raise WorkspacePermissionDeniedError(
            "You do not have permission to change workspace settings."
        )

    resolved_slug: str | None = None
    if slug is not None and slug != workspace.slug:
        resolved_slug = validate_slug(slug)
        if not workspace_crud.is_workspace_slug_available(
            db, organization_id=workspace.organization_id, slug=resolved_slug
        ):
            raise WorkspaceAlreadyExistsError(
                f"A workspace with the identifier '{resolved_slug}' already "
                "exists in this organization."
            )

    try:
        updated = workspace_crud.update_workspace(
            db,
            workspace=workspace,
            workspace_name=workspace_name,
            slug=resolved_slug,
            timezone=timezone,
            language=language,
            currency=currency,
            date_format=date_format,
            company_logo_url=company_logo_url,
        )
        commit_and_refresh(db, updated)

        logger.info(
            "AUDIT | WORKSPACE_UPDATED | Workspace: %s | Actor: %s",
            workspace.id,
            actor_id,
        )
        return updated

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to update workspace %s: %s",
            workspace.id,
            str(exc),
            exc=exc,
        )


def remove_workspace_logo(
    db: Session,
    *,
    workspace: Workspace,
    effective_role: WorkspaceRole,
    actor_id: uuid.UUID,
) -> Workspace:
    """
    Clears the workspace logo reference.

    Explicit operation rather than passing None to update_workspace_settings,
    which reserves None for "unchanged". Deleting the underlying file is
    handled by the upload service.
    """
    if not can_manage_workspace_settings(effective_role):
        raise WorkspacePermissionDeniedError(
            "You do not have permission to change workspace settings."
        )

    try:
        updated = workspace_crud.clear_workspace_logo(db, workspace=workspace)
        commit_and_refresh(db, updated)

        logger.info(
            "AUDIT | WORKSPACE_LOGO_REMOVED | Workspace: %s | Actor: %s",
            workspace.id,
            actor_id,
        )
        return updated

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to remove logo for workspace %s: %s",
            workspace.id,
            str(exc),
            exc=exc,
        )


def archive_workspace(
    db: Session,
    *,
    workspace: Workspace,
    actor_id: uuid.UUID,
    actor_organization_role: OrganizationRole,
) -> Workspace:
    """
    Soft-deletes a workspace.

    Authorized at organization level rather than workspace level: a workspace
    does not own itself, so its destruction is a decision for the tenant that
    does. A workspace ADMIN can configure and populate a workspace but cannot
    destroy it, mirroring GitHub, where repository admins cannot delete a
    repository if the organization restricts it.

    Never a hard delete. Data is retained and restorable within the
    organization's retention window.
    """
    if not can_delete_workspace(actor_organization_role):
        raise OrganizationPermissionDeniedError(
            "Only an organization owner or admin can archive a workspace."
        )

    active_count = workspace_crud.count_workspaces_for_organization(
        db,
        organization_id=workspace.organization_id,
        statuses=(WorkspaceStatus.ACTIVE,),
    )
    if active_count <= 1:
        raise WorkspacePermissionDeniedError(
            "An organization must retain at least one active workspace. "
            "Create another workspace before archiving this one."
        )

    try:
        archived = workspace_crud.set_workspace_status(
            db, workspace=workspace, status=WorkspaceStatus.ARCHIVED
        )
        commit_and_refresh(db, archived)

        logger.info(
            "AUDIT | WORKSPACE_ARCHIVED | Org: %s | Workspace: %s | Actor: %s",
            workspace.organization_id,
            workspace.id,
            actor_id,
        )
        return archived

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to archive workspace %s: %s",
            workspace.id,
            str(exc),
            exc=exc,
        )


def restore_workspace(
    db: Session,
    *,
    workspace: Workspace,
    actor_id: uuid.UUID,
    actor_organization_role: OrganizationRole,
) -> Workspace:
    """
    Restores an archived workspace to ACTIVE.

    The counterpart that makes archival a soft delete rather than a euphemism
    for one.
    """
    if not can_delete_workspace(actor_organization_role):
        raise OrganizationPermissionDeniedError(
            "Only an organization owner or admin can restore a workspace."
        )

    try:
        restored = workspace_crud.set_workspace_status(
            db, workspace=workspace, status=WorkspaceStatus.ACTIVE
        )
        commit_and_refresh(db, restored)

        logger.info(
            "AUDIT | WORKSPACE_RESTORED | Workspace: %s | Actor: %s",
            workspace.id,
            actor_id,
        )
        return restored

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to restore workspace %s: %s",
            workspace.id,
            str(exc),
            exc=exc,
        )