"""
Business orchestration for Workspaces within FlowPilot AI.
ARCH-14 Step 8 CONTRACT: Removed legacy cost parameters from workspace AI initialization.
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
from app.models.audit_log import AuditAction, AuditResourceType
from app.services import audit_service

from app.crud.ai_settings import upsert_ai_settings
from app.crud.document_settings import upsert_document_settings
from app.schemas.ai_settings import AISettingsUpdate, AIProvider
from app.schemas.document_settings import DocumentSettingsCreate

logger = logging.getLogger("app.services.workspace_service")

MAX_WORKSPACES_PER_ORGANIZATION: int = 10


def get_workspace_or_raise(
    db: Session,
    *,
    workspace_id: uuid.UUID,
) -> Workspace:
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
    workspace = workspace_crud.get_workspace_by_slug(
        db, organization_id=organization.id, slug=slug
    )
    if workspace is None:
        raise WorkspaceNotFoundError("Workspace not found.")
    return workspace


def assert_workspace_operational(workspace: Workspace) -> None:
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
    if not can_create_workspace(actor_organization_role):
        raise OrganizationPermissionDeniedError(
            "You do not have permission to create workspaces in this organization."
        )

    existing_count = workspace_crud.count_workspaces_for_organization(
        db,
        organization_id=organization.id,
        statuses=(WorkspaceStatus.ACTIVE,),
    )
    if existing_count >= MAX_WORKSPACES_PER_ORGANIZATION:
        raise OrganizationPermissionDeniedError(
            f"This organization has reached its limit of {MAX_WORKSPACES_PER_ORGANIZATION} workspaces."
        )

    if slug is not None:
        resolved_slug = validate_slug(slug)
        if not workspace_crud.is_workspace_slug_available(
            db, organization_id=organization.id, slug=resolved_slug
        ):
            raise WorkspaceAlreadyExistsError(
                f"A workspace with the identifier '{resolved_slug}' already exists in this organization."
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

        workspace_members_crud.create_workspace_member(
            db,
            workspace_id=workspace.id,
            user_id=actor_id,
            role=WorkspaceRole.ADMIN,
        )

        upsert_ai_settings(
            db,
            workspace_id=workspace.id,
            updated_by_user_id=actor_id,
            settings_in=AISettingsUpdate(
                provider=AIProvider.GROQ,
                model="mixtral-8x7b-32768",
                temperature=0.7,
                max_output_tokens=2048,
                top_p=1.0,
                frequency_penalty=0.0,
                presence_penalty=0.0,
                system_prompt_version="v1",
                prompt_version="v1",
                enable_token_tracking=True,
                enable_streaming=True,
            )
        )

        upsert_document_settings(
            db,
            workspace_id=workspace.id,
            updated_by_user_id=actor_id,
            settings_in=DocumentSettingsCreate()
        )

        audit_service.record(
            db,
            organization_id=organization.id,
            workspace_id=workspace.id,
            actor_id=actor_id,
            resource_type=AuditResourceType.WORKSPACE,
            resource_id=workspace.id,
            action=AuditAction.CREATED,
            details={
                "name": workspace.workspace_name,
                "slug": workspace.slug,
            },
        )

        commit_and_refresh(db, workspace)
        return workspace

    except IntegrityError as exc:
        db.rollback()
        raise WorkspaceAlreadyExistsError(
            f"The identifier '{resolved_slug}' was just taken. Please try again."
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
) -> Workspace:
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
                f"A workspace with the identifier '{resolved_slug}' already exists in this organization."
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
        )

        audit_service.record(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            actor_id=actor_id,
            resource_type=AuditResourceType.WORKSPACE,
            resource_id=workspace.id,
            action=AuditAction.UPDATED,
            details={
                "workspace_name": updated.workspace_name,
                "slug": updated.slug,
            },
        )

        commit_and_refresh(db, updated)
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
    if not can_manage_workspace_settings(effective_role):
        raise WorkspacePermissionDeniedError(
            "You do not have permission to change workspace settings."
        )

    try:
        updated = workspace_crud.clear_workspace_logo(db, workspace=workspace)

        audit_service.record(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            actor_id=actor_id,
            resource_type=AuditResourceType.UPLOADED_FILE,
            resource_id=workspace.id,
            action=AuditAction.DELETED,
            details={"kind": "WORKSPACE_LOGO"},
        )

        commit_and_refresh(db, updated)
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
            "An organization must retain at least one active workspace. Create another workspace before archiving this one."
        )

    try:
        archived = workspace_crud.set_workspace_status(
            db, workspace=workspace, status=WorkspaceStatus.ARCHIVED
        )

        audit_service.record(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            actor_id=actor_id,
            resource_type=AuditResourceType.WORKSPACE,
            resource_id=workspace.id,
            action=AuditAction.ARCHIVED,
            details={"slug": workspace.slug},
        )

        commit_and_refresh(db, archived)
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
    if not can_delete_workspace(actor_organization_role):
        raise OrganizationPermissionDeniedError(
            "Only an organization owner or admin can restore a workspace."
        )

    try:
        restored = workspace_crud.set_workspace_status(
            db, workspace=workspace, status=WorkspaceStatus.ACTIVE
        )

        audit_service.record(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            actor_id=actor_id,
            resource_type=AuditResourceType.WORKSPACE,
            resource_id=workspace.id,
            action=AuditAction.RESTORED,
            details={"slug": workspace.slug},
        )

        commit_and_refresh(db, restored)
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