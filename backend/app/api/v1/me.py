"""
Actor-scoped API router for FlowPilot AI.

Answers "who am I and what can I reach" without naming a tenant. Every route
here requires authentication and nothing more.

ARCH-08 Step 1 (Regression R-A Fix): WorkspaceSummary now explicitly passes
logo_file_id=workspace.logo_file_id so company_logo_url property computes correctly.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from app.api import deps
from app.core.workspace_permissions import resolve_effective_workspace_role
from app.crud import organization_members as organization_members_crud
from app.crud import workspace as workspace_crud
from app.crud import workspace_members as workspace_members_crud
from app.crud.membership_filters import ACTIVE_ONLY
from app.schemas.me import (
    MeContextResponse,
    MeUser,
    OrganizationMembershipSummary,
)
from app.schemas.organization import OrganizationResponse
from app.schemas.user import UserProfileResponse, UserProfileUpdate
from app.schemas.workspace import WorkspaceSummary
from app.models.workspace import WorkspaceStatus
from app.services import organization_service
from app.services import user_service
from app.services import workspace_service

logger = logging.getLogger("app.api.v1.me")

router = APIRouter(tags=["Me"])


@router.get(
    "/me/organizations",
    response_model=list[OrganizationResponse],
    summary="List My Organizations",
)
async def list_my_organizations(
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    """
    Returns every organization the actor actively belongs to.
    """
    return organization_service.list_organizations_for_user(
        db, user_id=current_user.id
    )


@router.get(
    "/me/context",
    response_model=MeContextResponse,
    summary="Bootstrap Context",
)
async def get_my_context(
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    """
    Returns the complete bootstrap context in one round trip.
    """
    memberships = organization_members_crud.list_memberships_for_user(
        db, user_id=current_user.id, statuses=ACTIVE_ONLY
    )

    summaries: list[OrganizationMembershipSummary] = []

    for membership in memberships:
        organization = membership.organization
        if organization is None:
            continue

        workspaces = workspace_service.list_accessible_workspaces(
            db,
            organization=organization,
            user_id=current_user.id,
            organization_role=membership.role,
        )

        workspace_summaries: list[WorkspaceSummary] = []
        for workspace in workspaces:
            grant = workspace_members_crud.get_workspace_member(
                db,
                workspace_id=workspace.id,
                user_id=current_user.id,
                statuses=ACTIVE_ONLY,
            )
            effective_role = resolve_effective_workspace_role(
                membership.role, grant.role if grant else None
            )
            if effective_role is None:
                continue

            workspace_summaries.append(
                WorkspaceSummary(
                    id=workspace.id,
                    organization_id=workspace.organization_id,
                    slug=workspace.slug,
                    workspace_name=workspace.workspace_name,
                    status=workspace.status,
                    logo_file_id=workspace.logo_file_id,
                    effective_role=effective_role,
                )
            )

        summaries.append(
            OrganizationMembershipSummary(
                organization_id=organization.id,
                organization_slug=organization.slug,
                organization_name=organization.name,
                organization_status=organization.status,
                role=membership.role,
                workspaces=workspace_summaries,
            )
        )

    default_organization_id = summaries[0].organization_id if summaries else None
    default_workspace_id = (
        summaries[0].workspaces[0].id
        if summaries and summaries[0].workspaces
        else None
    )

    return MeContextResponse(
        user=MeUser.model_validate(current_user),
        organizations=summaries,
        default_organization_id=default_organization_id,
        default_workspace_id=default_workspace_id,
        requires_onboarding=len(summaries) == 0,
    )


@router.get(
    "/me/workspaces",
    response_model=list[WorkspaceSummary],
    summary="List My Workspace Grants",
)
async def list_my_workspaces(
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    """
    Returns every workspace the actor holds an explicit grant on, across all
    organizations.
    """
    grants = workspace_members_crud.list_memberships_for_user(
        db, user_id=current_user.id, statuses=ACTIVE_ONLY
    )

    summaries: list[WorkspaceSummary] = []
    for grant in grants:
        workspace = workspace_crud.get_workspace_by_id(
            db, workspace_id=grant.workspace_id
        )
        if workspace is None or workspace.status is not WorkspaceStatus.ACTIVE:
            continue
        summaries.append(
            WorkspaceSummary(
                id=workspace.id,
                organization_id=workspace.organization_id,
                slug=workspace.slug,
                workspace_name=workspace.workspace_name,
                status=workspace.status,
                logo_file_id=workspace.logo_file_id,
                effective_role=grant.role,
            )
        )
    return summaries


# ============================================================================
# Profile (ARCH-05 Step 5)
# ============================================================================

@router.get(
    "/me/profile",
    response_model=UserProfileResponse,
    summary="Get My Profile",
)
async def get_my_profile(
    current_user: deps.CurrentUser,
) -> Any:
    """
    Returns the actor's own profile.
    """
    return user_service.get_user_profile(current_user)


@router.patch(
    "/me/profile",
    response_model=UserProfileResponse,
    summary="Update My Profile",
)
async def update_my_profile(
    payload: UserProfileUpdate,
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    """
    Updates display_name, timezone, and/or locale.
    """
    return user_service.update_user_profile(
        db,
        user=current_user,
        display_name=payload.display_name,
        timezone=payload.timezone,
        locale=payload.locale,
    )
