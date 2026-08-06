"""
Business orchestration for the Organization tenant root.

Owns transaction boundaries for organization provisioning and lifecycle.
Authorization decisions delegate to app.core.organization_permissions;
persistence delegates to app.crud.

Provisioning is the operation that replaces onboarding. Before ARCH-01, a
single PUT endpoint conflated "found a company" with "edit settings", which is
why an existing owner revisiting the onboarding screen silently overwrote their
live workspace configuration. Those are separate domain operations and are now
separate functions.

Structural rule observed throughout this module: validate before the try block,
mutate inside it. rollback_and_log_error re-raises via logger.exception, so a
domain rejection raised inside the try would be logged at ERROR with a full
traceback — burying real failures under ordinary business outcomes.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

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
from app.crud import organization as organization_crud
from app.crud import organization_members as organization_members_crud
from app.crud import workspace as workspace_crud
from app.crud import workspace_members as workspace_members_crud
from app.crud.membership_filters import ACTIVE_ONLY
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole

logger = logging.getLogger("app.services.organization_service")

#: Maximum organizations a single account may found.
#:
#: An abuse control, not an RBAC rule. Founding an organization is an
#: account-level capability available to any authenticated user, exactly as in
#: Slack, Notion, Linear, and GitHub; a Viewer in one tenant may still found
#: their own. ARCH-05 replaces this constant with a plan-derived limit.
MAX_ORGANIZATIONS_PER_USER: int = 3

#: Name given to the workspace created alongside a new organization when the
#: caller does not supply one.
DEFAULT_WORKSPACE_NAME: str = "General"


@dataclass(frozen=True)
class ProvisionedOrganization:
    """
    The complete result of provisioning a tenant.

    Returned as a unit because the caller needs every part: the organization
    for the response body, the workspace to redirect into, and both memberships
    to establish the initial request context without a second round trip.
    """
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
    """
    Creates a tenant: an organization, its first workspace, and the founder's
    memberships in both. Atomic — all five records commit together or none do.

    Args:
        organization_slug: An explicitly chosen slug. When omitted, one is
            derived from organization_name with collision resolution.

    Raises:
        OrganizationPermissionDeniedError: The account has reached its limit.
        InvalidSlugError / ReservedSlugError: The supplied slug is unusable.
        OrganizationAlreadyExistsError: The slug was claimed concurrently.
    """
    # --- Validation before mutation ----------------------------------------
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

    # A new organization has no sibling workspaces, so the first slug within it
    # cannot collide. The predicate is supplied for correctness, not necessity.
    workspace_slug = generate_unique_slug(
        resolved_workspace_name,
        is_available=lambda candidate: True,
        fallback_prefix="workspace",
    )

    # --- Mutation ----------------------------------------------------------
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

        # Written despite the founder's derived elevation as organization
        # OWNER: they must appear in the workspace member list, and a later
        # demotion to MEMBER must not silently strip their workspace access.
        workspace_membership = (
            workspace_members_crud.create_workspace_member(
                db,
                workspace_id=workspace.id,
                user_id=user_id,
                role=WorkspaceRole.ADMIN,
                status=MembershipStatus.ACTIVE,
            )
        )

        commit_and_refresh(db, organization)
        db.refresh(workspace)
        db.refresh(organization_membership)
        db.refresh(workspace_membership)

        logger.info(
            "AUDIT | ORGANIZATION_PROVISIONED | Org: %s (%s) | "
            "Workspace: %s (%s) | Owner: %s",
            organization.id,
            organization.slug,
            workspace.id,
            workspace.slug,
            user_id,
        )

        return ProvisionedOrganization(
            organization=organization,
            workspace=workspace,
            organization_membership=organization_membership,
            workspace_membership=workspace_membership,
        )

    except IntegrityError as exc:
        # The availability check above is advisory: two concurrent requests can
        # both observe a free slug. The unique index is the authority, and the
        # client is expected to retry.
        db.rollback()
        logger.warning(
            "Organization provisioning lost a slug race on '%s' for user %s.",
            slug,
            user_id,
        )
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
    """
    Fetches an organization, raising if it is missing.

    Does not authorize. The request context resolves membership separately and
    converts a non-member into a 404 rather than a 403, so the response cannot
    be used to confirm a tenant exists.
    """
    organization = organization_crud.get_organization_by_id(
        db, organization_id=organization_id
    )
    if organization is None:
        raise OrganizationNotFoundError("Organization not found.")
    return organization


def assert_organization_operational(organization: Organization) -> None:
    """
    Rejects requests against a suspended or archived tenant.

    Raises TenantSuspendedError, which maps to 403 with a distinguishable error
    code so the client can render an explanatory tombstone instead of a generic
    failure.
    """
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
    """
    Returns every organization the user actively belongs to.

    Multiple results are ordinary. The pre-ARCH-01 design could not represent
    this at all: a second membership crashed the account.
    """
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
) -> Organization:
    """
    Updates organization identity and branding.

    Changing the slug changes the tenant's public URL. ARCH-04 adds slug
    history with redirects; until then the old address stops resolving
    immediately, which is why the change is logged as an audit event.
    """
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
        commit_and_refresh(db, updated)

        if resolved_slug is not None:
            logger.info(
                "AUDIT | ORGANIZATION_SLUG_CHANGED | Org: %s | New slug: %s",
                organization.id,
                resolved_slug,
            )

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
    """
    Soft-deletes an organization by setting its status to ARCHIVED.

    Never a hard delete. The tenant and all its data are retained for the
    contractual retention window, and the operation is reversible by an
    operator. ARCH-05 attaches subscription termination; ARCH-07 records the
    event in the audit log.
    """
    if not can_delete_organization(actor_role):
        raise OrganizationPermissionDeniedError(
            "Only an organization owner can delete the organization."
        )

    try:
        archived = organization_crud.set_organization_status(
            db,
            organization=organization,
            status=OrganizationStatus.ARCHIVED,
        )
        commit_and_refresh(db, archived)

        logger.info(
            "AUDIT | ORGANIZATION_ARCHIVED | Org: %s (%s) | Actor: %s",
            organization.id,
            organization.slug,
            actor_id,
        )
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