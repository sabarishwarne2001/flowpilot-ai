"""
Workspace API router for FlowPilot AI.

Every route is addressed as /workspaces/{workspace_id}/... The workspace
identifier is taken directly rather than nested under the organization, because
a workspace identifier already determines its organization; requiring both
would create two sources of truth and an inconsistency check on every request.

Two legacy endpoints are deliberately NOT reproduced here:

  PUT /workspace
      Created a workspace when none existed and updated one when it did. That
      conflation is why an existing owner revisiting the onboarding screen
      silently overwrote their live settings. Creation is now
      POST /organizations (first workspace) or
      POST /organizations/{id}/workspaces (additional).

  GET /workspace/public
      Returned the oldest workspace row in the database to anonymous callers,
      disclosing one tenant's name, logo, and locale to every visitor.

This file is new rather than a replacement of app/api/v1/workspace.py, which
also hosts the invitation endpoints. Step 9c lifts those out, applies the
authentication fix, and removes the legacy module. Neither router is registered
until then, so no route collision can occur.

Routes carry their full path. Register with no prefix.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api import deps
from app.core.exceptions import (
    InvalidSlugError,
    ReservedSlugError,
    WorkspaceMemberError,
)
from app.core.slugs import validate_slug
from app.crud import organization_members as organization_members_crud
from app.crud import workspace as workspace_crud
from app.crud import workspace_members as workspace_members_crud
from app.crud.membership_filters import ACTIVE_ONLY
from app.models.organization import OrganizationRole
from app.models.workspace import WorkspaceRole
from app.schemas.common import MessageResponse
from app.schemas.organization import UserSummary
from app.schemas.workspace import (
    WorkspaceResponse,
    WorkspaceSlugAvailabilityResponse,
    WorkspaceUpdate,
)
from app.schemas.workspace_member import (
    WorkspaceMemberGrant,
    WorkspaceMemberListResponse,
    WorkspaceMemberResponse,
    WorkspaceMemberRoleUpdate,
)
from app.services import workspace_member_service
from app.services import workspace_service

logger = logging.getLogger("app.api.v1.workspaces")

router = APIRouter(tags=["Workspaces"])


def _serialize_grant(membership) -> WorkspaceMemberResponse:
    """Serializes a stored workspace grant."""
    return WorkspaceMemberResponse(
        id=membership.id,
        workspace_id=membership.workspace_id,
        user=UserSummary.model_validate(membership.user),
        role=membership.role,
        status=membership.status,
        is_derived=False,
        created_at=membership.created_at,
    )


# ============================================================================
# Workspace
# ============================================================================

@router.get(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
    summary="Get Workspace",
)
async def get_workspace(context: deps.WorkspaceCtx) -> Any:
    """
    Returns the addressed workspace.

    Any effective role may read it. A caller with no access receives 404 rather
    than 403, so the response cannot confirm the workspace exists.
    """
    return context.workspace


@router.patch(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
    summary="Update Workspace Settings",
)
async def update_workspace(
    payload: WorkspaceUpdate,
    db: deps.DbSession,
    context=Depends(deps.RequireWorkspaceAdmin),
) -> Any:
    """
    Updates workspace name, locale, and branding.

    Authorized by effective role, so an organization administrator may edit any
    workspace through their derived grant without holding a stored row.
    """
    return workspace_service.update_workspace_settings(
        db,
        workspace=context.workspace,
        effective_role=context.effective_workspace_role,
        actor_id=context.user_id,
        workspace_name=payload.workspace_name,
        slug=payload.slug,
        timezone=payload.timezone,
        language=payload.language,
        currency=payload.currency,
        date_format=payload.date_format,
        company_logo_url=payload.company_logo_url,
    )


@router.delete(
    "/workspaces/{workspace_id}/logo",
    response_model=WorkspaceResponse,
    summary="Remove Workspace Logo",
)
async def remove_workspace_logo(
    db: deps.DbSession,
    context=Depends(deps.RequireWorkspaceAdmin),
) -> Any:
    """
    Clears the workspace logo.

    A separate endpoint rather than a null in PATCH, which reserves null for
    "leave unchanged".
    """
    return workspace_service.remove_workspace_logo(
        db,
        workspace=context.workspace,
        effective_role=context.effective_workspace_role,
        actor_id=context.user_id,
    )


@router.get(
    "/workspaces/{workspace_id}/slug-available",
    response_model=WorkspaceSlugAvailabilityResponse,
    summary="Check Workspace Slug Availability",
)
async def check_workspace_slug(
    db: deps.DbSession,
    context=Depends(deps.RequireWorkspaceAdmin),
    slug: str = Query(..., description="Candidate workspace identifier."),
) -> Any:
    """
    Advisory availability check, scoped to the parent organization.

    Two tenants may both have a workspace called "engineering": uniqueness is
    per organization, not global.
    """
    try:
        normalized = validate_slug(slug)
    except (InvalidSlugError, ReservedSlugError) as exc:
        return WorkspaceSlugAvailabilityResponse(
            slug=slug.strip().lower(), available=False, reason=str(exc)
        )

    available = workspace_crud.is_workspace_slug_available(
        db, organization_id=context.organization_id, slug=normalized
    )
    return WorkspaceSlugAvailabilityResponse(
        slug=normalized,
        available=available,
        reason=None if available else "Already used in this organization.",
    )


@router.post(
    "/workspaces/{workspace_id}/archive",
    response_model=WorkspaceResponse,
    summary="Archive Workspace",
)
async def archive_workspace(
    db: deps.DbSession,
    context: deps.WorkspaceCtx,
) -> Any:
    """
    Soft-deletes the workspace.

    Authorized at organization level rather than workspace level: a workspace
    does not own itself, so its destruction is a decision for the tenant that
    does. A workspace ADMIN can configure and populate it but cannot destroy
    it. The service also refuses to archive an organization's last active
    workspace.
    """
    return workspace_service.archive_workspace(
        db,
        workspace=context.workspace,
        actor_id=context.user_id,
        actor_organization_role=context.organization_role,
    )


@router.post(
    "/workspaces/{workspace_id}/restore",
    response_model=WorkspaceResponse,
    summary="Restore Workspace",
)
async def restore_workspace(
    db: deps.DbSession,
    context: deps.WorkspaceCtx,
) -> Any:
    """
    Restores an archived workspace.

    The counterpart that makes archival a soft delete rather than a euphemism
    for one.
    """
    return workspace_service.restore_workspace(
        db,
        workspace=context.workspace,
        actor_id=context.user_id,
        actor_organization_role=context.organization_role,
    )


# ============================================================================
# Workspace members
# ============================================================================

@router.get(
    "/workspaces/{workspace_id}/members",
    response_model=WorkspaceMemberListResponse,
    summary="List Workspace Members",
)
async def list_workspace_members(
    db: deps.DbSession,
    context: deps.WorkspaceCtx,
) -> Any:
    """
    Returns everyone with access to this workspace.

    Merges explicit grants with organization OWNER and ADMIN members holding
    derived ADMIN. A directory of stored rows alone would omit the people with
    the most access — accurate to the schema, misleading to the user. Derived
    entries are marked is_derived so the client can disable revocation, which
    is not meaningful at workspace level for them.
    """
    grants = workspace_member_service.list_workspace_members(
        db, workspace=context.workspace
    )
    granted_user_ids = {grant.user_id for grant in grants}

    items: list[WorkspaceMemberResponse] = [
        _serialize_grant(grant) for grant in grants
    ]

    org_members = organization_members_crud.list_organization_members(
        db, organization_id=context.organization_id, statuses=ACTIVE_ONLY
    )
    for member in org_members:
        if member.user_id in granted_user_ids:
            continue
        if member.role not in (OrganizationRole.OWNER, OrganizationRole.ADMIN):
            continue
        items.append(
            WorkspaceMemberResponse(
                id=None,
                workspace_id=context.workspace_id,
                user=UserSummary.model_validate(member.user),
                role=WorkspaceRole.ADMIN,
                status=member.status,
                is_derived=True,
                organization_role=member.role,
                created_at=None,
            )
        )

    return WorkspaceMemberListResponse(items=items, total=len(items))


@router.post(
    "/workspaces/{workspace_id}/members",
    response_model=WorkspaceMemberResponse,
    summary="Grant Workspace Access",
)
async def grant_workspace_access(
    payload: WorkspaceMemberGrant,
    db: deps.DbSession,
    context=Depends(deps.RequireWorkspaceAdmin),
) -> Any:
    """
    Grants an existing organization member access to this workspace.

    The target must already hold an ACTIVE organization membership: the seat is
    what authorizes their presence in the tenant. A previously revoked grant is
    reactivated rather than duplicated, keeping the member's history on one
    record.

    Granting ADMIN additionally requires organization-level standing.
    """
    access = workspace_member_service.resolve_workspace_access(
        db, workspace=context.workspace, user_id=context.user_id
    )
    membership = workspace_member_service.grant_workspace_access(
        db,
        workspace=context.workspace,
        actor_access=access,
        target_user_id=payload.user_id,
        role=payload.role,
    )
    return _serialize_grant(membership)


@router.patch(
    "/workspaces/{workspace_id}/members/{membership_id}",
    response_model=WorkspaceMemberResponse,
    summary="Change Workspace Member Role",
)
async def change_workspace_member_role(
    membership_id: UUID,
    payload: WorkspaceMemberRoleUpdate,
    db: deps.DbSession,
    context=Depends(deps.RequireWorkspaceAdmin),
) -> Any:
    """
    Changes an existing workspace grant.

    Granting or revoking ADMIN requires organization-level standing, which
    resolves the deadlock two workspace admins would otherwise create: neither
    able to manage the other, with no higher workspace role to break the tie.
    """
    target = workspace_members_crud.get_workspace_member_by_id(
        db, workspace_id=context.workspace_id, membership_id=membership_id
    )
    if target is None:
        raise WorkspaceMemberError("Workspace membership not found.")

    access = workspace_member_service.resolve_workspace_access(
        db, workspace=context.workspace, user_id=context.user_id
    )
    membership = workspace_member_service.change_workspace_member_role(
        db,
        workspace=context.workspace,
        actor_access=access,
        target_membership=target,
        new_role=payload.role,
    )
    return _serialize_grant(membership)


@router.post(
    "/workspaces/{workspace_id}/members/{membership_id}/revoke",
    response_model=WorkspaceMemberResponse,
    summary="Revoke Workspace Access",
)
async def revoke_workspace_access(
    membership_id: UUID,
    db: deps.DbSession,
    context=Depends(deps.RequireWorkspaceAdmin),
) -> Any:
    """
    Revokes a workspace grant, retaining the row.

    The organization seat is untouched: losing access to one workspace is not
    the same as leaving the company, and conflating them would make a routine
    reassignment look like a termination.
    """
    target = workspace_members_crud.get_workspace_member_by_id(
        db, workspace_id=context.workspace_id, membership_id=membership_id
    )
    if target is None:
        raise WorkspaceMemberError("Workspace membership not found.")

    access = workspace_member_service.resolve_workspace_access(
        db, workspace=context.workspace, user_id=context.user_id
    )
    membership = workspace_member_service.revoke_workspace_access(
        db,
        workspace=context.workspace,
        actor_access=access,
        target_membership=target,
    )
    return _serialize_grant(membership)


@router.post(
    "/workspaces/{workspace_id}/leave",
    response_model=MessageResponse,
    summary="Leave Workspace",
)
async def leave_workspace(
    db: deps.DbSession,
    context: deps.WorkspaceCtx,
) -> Any:
    """
    Removes the acting user's own workspace grant.

    An organization administrator retains derived access afterward, so this
    removes them from the member list without cutting them off. That matches
    GitHub, where an organization owner cannot lock themselves out of a
    repository they administer.
    """
    access = workspace_member_service.resolve_workspace_access(
        db, workspace=context.workspace, user_id=context.user_id
    )
    workspace_member_service.leave_workspace(
        db, workspace=context.workspace, access=access
    )
    return MessageResponse(message="You have left the workspace.")