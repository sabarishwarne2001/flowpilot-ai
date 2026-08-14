"""
Business orchestration for the Organization tenant root.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    OrganizationAlreadyExistsError,
    OrganizationNotFoundError,
    OrganizationPermissionDeniedError,
    TenantSuspendedError,
)
from app.core.organization_permissions import (
    can_delete_organization,
    can_manage_organization_settings,
)
from app.core.slugs import generate_unique_slug, validate_slug
from app.core.transactions import commit_and_refresh, rollback_and_log_error
from app.crud import api_key as api_key_crud
from app.crud import organization as organization_crud
from app.crud import organization_members as organization_members_crud
from app.crud import workspace as workspace_crud
from app.crud import workspace_members as workspace_members_crud
from app.crud.membership_filters import ACTIVE_ONLY
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole
from app.services import audit_service

logger = logging.getLogger("app.services.organization_service")

MAX_ORGANIZATIONS_PER_USER: int = 3
DEFAULT_WORKSPACE_NAME: str = "General"


@dataclass(frozen=True)
class ProvisionedOrganization:
    organization: Organization
    workspace: Workspace
    organization_membership: OrganizationMember
    workspace_membership: WorkspaceMember


def provision_organization(
    db: Session,
    *,
    user_id: uuid.UUID,
    organization_name: str,
    workspace_name: str | None = None,
    organization_slug: str | None = None,
    legal_name: str | None = None,
    timezone: str = "UTC",
    language: str = "en",
    currency: str = "USD",
    date_format: str = "YYYY-MM-DD",
) -> ProvisionedOrganization:
    owned = organization_crud.count_organizations_owned_by_user(
        db, user_id=user_id
    )
    if owned >= MAX_ORGANIZATIONS_PER_USER:
        raise OrganizationPermissionDeniedError(
            f"You have reached the limit of {MAX_ORGANIZATIONS_PER_USER} "
            "organizations for this account."
        )

    if organization_slug is not None:
        slug = validate_slug(organization_slug)
        if not organization_crud.is_organization_slug_available(db, slug=slug):
            raise OrganizationAlreadyExistsError(
                f"The identifier '{slug}' is already taken."
            )
    else:
        slug = generate_unique_slug(
            organization_name,
            is_available=lambda candidate: (
                organization_crud.is_organization_slug_available(
                    db, slug=candidate
                )
            ),
            fallback_prefix="org",
        )

    resolved_workspace_name = (
        workspace_name or ""
    ).strip() or DEFAULT_WORKSPACE_NAME

    workspace_slug = generate_unique_slug(
        resolved_workspace_name,
        is_available=lambda candidate: True,
        fallback_prefix="workspace",
    )

    try:
        organization = organization_crud.create_organization(
            db,
            slug=slug,
            name=organization_name.strip(),
            legal_name=legal_name,
            status=OrganizationStatus.ACTIVE,
        )

        organization_membership = (
            organization_members_crud.create_organization_member(
                db,
                organization_id=organization.id,
                user_id=user_id,
                role=OrganizationRole.OWNER,
                status=MembershipStatus.ACTIVE,
            )
        )

        workspace = workspace_crud.create_workspace(
            db,
            organization_id=organization.id,
            slug=workspace_slug,
            workspace_name=resolved_workspace_name,
            timezone=timezone,
            language=language,
            currency=currency,
            date_format=date_format,
        )

        workspace_membership = (
            workspace_members_crud.create_workspace_member(
                db,
                workspace_id=workspace.id,
                user_id=user_id,
                role=WorkspaceRole.ADMIN,
                status=MembershipStatus.ACTIVE,
            )
        )

        audit_service.record(
            db,
            organization_id=organization.id,
            workspace_id=workspace.id,
            actor_id=user_id,
            resource_type=AuditResourceType.ORGANIZATION,
            resource_id=organization.id,
            action=AuditAction.CREATED,
            details={
                "name": organization.name,
                "slug": organization.slug,
                "initial_workspace_id": str(workspace.id),
                "initial_workspace_slug": workspace.slug,
            },
        )

        commit_and_refresh(db, organization)
        db.refresh(workspace)
        db.refresh(organization_membership)
        db.refresh(workspace_membership)

        return ProvisionedOrganization(
            organization=organization,
            workspace=workspace,
            organization_membership=organization_membership,
            workspace_membership=workspace_membership,
        )

    except IntegrityError as exc:
        db.rollback()
        raise OrganizationAlreadyExistsError(
            f"The identifier '{slug}' was just taken. Please try again."
        ) from exc

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to provision organization '%s' for user %s: %s",
            organization_name,
            user_id,
            str(exc),
            exc=exc,
        )


def get_organization_or_raise(
    db: Session,
    *,
    organization_id: uuid.UUID,
) -> Organization:
    organization = organization_crud.get_organization_by_id(
        db, organization_id=organization_id
    )
    if organization is None:
        raise OrganizationNotFoundError("Organization not found.")
    return organization


def assert_organization_operational(organization: Organization) -> None:
    if organization.status is not OrganizationStatus.ACTIVE:
        raise TenantSuspendedError(
            f"This organization is {organization.status.value.lower()} "
            "and cannot be accessed."
        )


def list_organizations_for_user(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> list[Organization]:
    return organization_crud.list_organizations_for_user(
        db, user_id=user_id, statuses=ACTIVE_ONLY
    )


def update_organization_settings(
    db: Session,
    *,
    organization: Organization,
    actor_role: OrganizationRole,
    name: str | None = None,
    legal_name: str | None = None,
    slug: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> Organization:
    if not can_manage_organization_settings(actor_role):
        raise OrganizationPermissionDeniedError(
            "You do not have permission to change organization settings."
        )

    resolved_slug: str | None = None
    if slug is not None and slug != organization.slug:
        resolved_slug = validate_slug(slug)
        if not organization_crud.is_organization_slug_available(
            db, slug=resolved_slug
        ):
            raise OrganizationAlreadyExistsError(
                f"The identifier '{resolved_slug}' is already taken."
            )

    try:
        updated = organization_crud.update_organization(
            db,
            organization=organization,
            name=name,
            legal_name=legal_name,
            slug=resolved_slug,
        )

        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=actor_id,
            resource_type=AuditResourceType.ORGANIZATION,
            resource_id=organization.id,
            action=AuditAction.UPDATED,
            details={
                "name": updated.name,
                "slug": updated.slug,
                "legal_name": updated.legal_name,
            },
        )

        commit_and_refresh(db, updated)
        return updated

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to update organization %s: %s",
            organization.id,
            str(exc),
            exc=exc,
        )


def archive_organization(
    db: Session,
    *,
    organization: Organization,
    actor_role: OrganizationRole,
    actor_id: uuid.UUID,
) -> Organization:
    if not can_delete_organization(actor_role):
        raise OrganizationPermissionDeniedError(
            "Only an organization owner can delete the organization."
        )

    try:
        # Deactivate all active API keys for this organization
        active_keys = api_key_crud.list_api_keys_for_organization(
            db, organization_id=organization.id, include_deactivated=False
        )
        now = datetime.now(UTC)
        for key in active_keys:
            key.deactivated_at = now
            key.deactivated_reason = "ORG_ARCHIVED"
            db.add(key)

        archived = organization_crud.set_organization_status(
            db,
            organization=organization,
            status=OrganizationStatus.ARCHIVED,
        )

        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=actor_id,
            resource_type=AuditResourceType.ORGANIZATION,
            resource_id=organization.id,
            action=AuditAction.ARCHIVED,
            details={
                "name": organization.name,
                "slug": organization.slug,
                "api_keys_deactivated": len(active_keys),
            },
        )

        commit_and_refresh(db, archived)
        return archived

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to archive organization %s: %s",
            organization.id,
            str(exc),
            exc=exc,
        )