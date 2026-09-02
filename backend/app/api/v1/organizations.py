"""
Organization API router for FlowPilot AI.

Exposes the commercial tenant surface: provisioning, settings, the member
directory, role management, and ownership transfer.

Thin by contract. Every handler validates its schema, delegates to a service,
and returns. Authorization is expressed declaratively through the dependency
guards in app.api.deps, so a route's permission requirement is visible in its
signature rather than buried in its body.

Routes carry their full path. Register with no prefix.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status

from app.api import deps
from app.core.exceptions import InvalidSlugError, ReservedSlugError
from app.core.slugs import validate_slug
from app.crud import organization as organization_crud
from app.crud import organization_members as organization_members_crud
from app.schemas.common import MessageResponse
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationMemberListResponse,
    OrganizationMemberResponse,
    OrganizationMemberRoleUpdate,
    OrganizationResponse,
    OrganizationUpdate,
    SlugAvailabilityResponse,
)
from app.schemas.workspace import WorkspaceCreate, WorkspaceResponse
from app.services import organization_member_service
from app.services import organization_service
from app.services import workspace_service
from app.services import organization_invitation_service

logger = logging.getLogger("app.api.v1.organizations")

router = APIRouter(tags=["Organizations"])


# ============================================================================
# Provisioning and discovery
# ============================================================================

@router.post(
    "/organizations",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision Organization",
)
async def create_organization(
    payload: OrganizationCreate,
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    """
    Provisions a tenant: an organization, its first workspace, and the
    founder's memberships in both. Atomic.

    Requires only an authenticated account. Founding an organization is an
    account-level capability, not a role permission — a Viewer in one tenant
    may still found their own, exactly as in Slack, Notion, Linear, and GitHub.
    """
    result = organization_service.provision_organization(
        db,
        user_id=current_user.id,
        organization_name=payload.organization_name,
        workspace_name=payload.workspace_name,
        organization_slug=payload.organization_slug,
        legal_name=payload.legal_name,
        timezone=payload.timezone,
        language=payload.language,
        currency=payload.currency,
        date_format=payload.date_format,
    )
    return result.organization


@router.get(
    "/organizations/slug-available",
    response_model=SlugAvailabilityResponse,
    summary="Check Organization Slug Availability",
)
async def check_organization_slug(
    db: deps.DbSession,
    current_user: deps.CurrentUser,
    slug: str = Query(..., description="Candidate organization identifier."),
) -> Any:
    """
    Advisory availability check for the creation form.

    Authenticated deliberately. An anonymous endpoint here would let a visitor
    enumerate which companies hold accounts — the same class of disclosure as
    the public workspace endpoint removed in ARCH-01.
    """
    try:
        normalized = validate_slug(slug)
    except (InvalidSlugError, ReservedSlugError) as exc:
        return SlugAvailabilityResponse(
            slug=slug.strip().lower(), available=False, reason=str(exc)
        )

    available = organization_crud.is_organization_slug_available(
        db, slug=normalized
    )
    return SlugAvailabilityResponse(
        slug=normalized,
        available=available,
        reason=None if available else "This identifier is already taken.",
    )


@router.get(
    "/organizations/{organization_id}",
    response_model=OrganizationResponse,
    summary="Get Organization",
)
async def get_organization(context: deps.OrgContext) -> Any:
    """
    Returns the addressed organization.

    Any active member may read it. A non-member receives 404 rather than 403,
    so the response cannot be used to confirm the organization exists.
    """
    return context.organization


@router.patch(
    "/organizations/{organization_id}",
    response_model=OrganizationResponse,
    summary="Update Organization Settings",
)
async def update_organization(
    payload: OrganizationUpdate,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    """
    Updates organization identity and branding.

    None means "leave unchanged". Changing the slug changes the tenant's public
    URL and the previous address stops resolving immediately.
    """
    return organization_service.update_organization_settings(
        db,
        organization=context.organization,
        actor_role=context.role,
        name=payload.name,
        legal_name=payload.legal_name,
        slug=payload.slug,
    )


@router.post(
    "/organizations/{organization_id}/archive",
    response_model=OrganizationResponse,
    summary="Archive Organization",
)
async def archive_organization(
    db: deps.DbSession,
    context=Depends(deps.RequireOrgOwner),
) -> Any:
    """
    Soft-deletes the organization.

    Named `archive` rather than exposed as DELETE because it is a reversible
    status transition, not a removal. Data is retained for the contractual
    retention window, and naming it accurately keeps that guarantee visible in
    the API surface.
    """
    return organization_service.archive_organization(
        db,
        organization=context.organization,
        actor_role=context.role,
        actor_id=context.user_id,
    )


# ============================================================================
# Workspaces within an organization
# ============================================================================

@router.get(
    "/organizations/{organization_id}/workspaces",
    response_model=list[WorkspaceResponse],
    summary="List Accessible Workspaces",
)
async def list_organization_workspaces(
    db: deps.ReadDbSession,
    context: deps.OrgContext,
) -> Any:
    """
    Returns the workspaces the actor may enter within this organization.

    Organization OWNER and ADMIN see every workspace through their derived
    grant; everyone else sees only those where they hold an explicit one.
    """
    return workspace_service.list_accessible_workspaces(
        db,
        organization=context.organization,
        user_id=context.user_id,
        organization_role=context.role,
    )


@router.post(
    "/organizations/{organization_id}/workspaces",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Workspace",
)
async def create_workspace(
    payload: WorkspaceCreate,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    """
    Creates an additional workspace inside this organization.

    Governed by organization role: a workspace does not create itself, and its
    parent tenant decides what exists inside it.
    """
    return workspace_service.create_workspace_in_organization(
        db,
        organization=context.organization,
        actor_id=context.user_id,
        actor_organization_role=context.role,
        workspace_name=payload.workspace_name,
        slug=payload.slug,
        timezone=payload.timezone,
        language=payload.language,
        currency=payload.currency,
        date_format=payload.date_format,
    )


# ============================================================================
# Members
# ============================================================================

@router.get(
    "/organizations/{organization_id}/members",
    response_model=OrganizationMemberListResponse,
    summary="List Organization Members",
)
async def list_organization_members(
    db: deps.ReadDbSession,
    context: deps.OrgContext,
    include_inactive: bool = Query(
        False,
        description="Include deactivated members. Administrators only.",
    ),
) -> Any:
    """
    Returns the member directory with the current seat count.

    Deactivated members are hidden by default: retained rows exist for
    attribution rather than everyday display.
    """
    members = organization_member_service.list_members(
        db,
        organization=context.organization,
        actor_role=context.role,
        include_inactive=include_inactive,
    )
    # ARCH-04 §D7.3 Unified seat-limit fix
    seats = organization_invitation_service.count_reserved_seats(
        db, organization_id=context.organization_id
    )
    return OrganizationMemberListResponse(
        items=[OrganizationMemberResponse.model_validate(m) for m in members],
        total=len(members),
        seats_consumed=seats,
    )


@router.patch(
    "/organizations/{organization_id}/members/{membership_id}",
    response_model=OrganizationMemberResponse,
    summary="Change Member Role",
)
async def change_member_role(
    membership_id: uuid.UUID,
    payload: OrganizationMemberRoleUpdate,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    target = organization_member_service.get_membership_or_raise(
        db,
        organization_id=context.organization_id,
        membership_id=membership_id,
    )
    return organization_member_service.change_member_role(
        db,
        organization=context.organization,
        actor_membership=context.membership,
        target_membership=target,
        new_role=payload.role,
    )


@router.post(
    "/organizations/{organization_id}/members/{membership_id}/deactivate",
    response_model=OrganizationMemberResponse,
    summary="Deactivate Organization Member",
)
async def deactivate_member(
    membership_id: uuid.UUID,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    target = organization_member_service.get_membership_or_raise(
        db,
        organization_id=context.organization_id,
        membership_id=membership_id,
    )
    return organization_member_service.deactivate_member(
        db,
        organization=context.organization,
        actor_membership=context.membership,
        target_membership=target,
    )


@router.post(
    "/organizations/{organization_id}/leave",
    response_model=MessageResponse,
    summary="Leave Organization",
)
async def leave_organization(
    db: deps.DbSession,
    context: deps.OrgContext,
) -> Any:
    organization_member_service.leave_organization(
        db,
        organization=context.organization,
        membership=context.membership,
    )
    return MessageResponse(message="You have left the organization.")


# ============================================================================
# REMOVED: POST /organizations/{organization_id}/transfer-ownership
#
# ARCH-05 Step 7. The single-phase endpoint that used to live here handed a
# tenant to another member on one authenticated request — no re-authentication
# (§B.2), no verified-target requirement (A.2.3), and above all no consent
# from the person taking on the organization's seat authority and its Phase F
# billing liability (§B.1).
#
# It is DELETED, not deprecated and not aliased, on the same reasoning
# router.py already records for the ARCH-02 flat routes: "an alias would keep
# the unscoped handler reachable, and the unscoped handler is the
# vulnerability." Leaving this route in place would have meant every
# protection ARCH-05 Steps 3 through 7 built was bypassable by calling the
# old path instead.
#
# Replaced by the two-phase flow in app/api/v1/ownership_transfers.py:
#     POST /organizations/{organization_id}/ownership-transfers
#     POST /organizations/{organization_id}/ownership-transfers/{id}/accept
#     POST /organizations/{organization_id}/ownership-transfers/{id}/decline
#     POST /organizations/{organization_id}/ownership-transfers/{id}/cancel
#
# organization_member_service.transfer_ownership itself is UNCHANGED and still
# very much in use — accept_transfer calls it verbatim. Only the unmediated
# route to it is gone.
# ============================================================================
