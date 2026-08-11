"""
Organization invitation API router for FlowPilot AI.

Exposes the ARCH-04 invitation lifecycle: issuance, resend, revocation, preview,
acceptance, and rejection.

Thin by contract. Every handler validates its schema, delegates to
app.services.organization_invitation_service, and returns. Authorization is
expressed declaratively through app.api.deps.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, status

from app.api import deps
from app.core.exceptions import SeatLimitExceededError
from app.core.links import (
    build_organization_invitations_link,
    build_organization_members_link,
)
from app.crud import user as user_crud
from app.schemas.common import MessageResponse
from app.schemas.organization_invitation import (
    AcceptedGrantSummary,
    InvitationCreateRequest,
    InvitationPreviewResponse,
    InvitationResponse,
    MyPendingInvitation,
    MyPendingInvitationsResponse,
    OrganizationInvitationAcceptResponse,
    OrganizationInvitationListResponse,
    OrganizationInvitationTokenRequest,
    WorkspacePreviewEntry,
)
from app.services import invitation_mail, organization_invitation_service

logger = logging.getLogger("app.api.v1.organization_invitations")

router = APIRouter(tags=["Invitations"])


# ============================================================================
# Issuance & Management
# ============================================================================

@router.post(
    "/organizations/{organization_id}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue Organization Invitation",
)
async def create_invitation(
    payload: InvitationCreateRequest,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    issued = organization_invitation_service.create_invitation(
        db,
        organization=context.organization,
        inviter=context.user,
        actor_role=context.role,
        email=payload.email,
        organization_role=payload.organization_role,
        grants=[(g.workspace_id, g.role) for g in payload.grants],
    )

    background_tasks.add_task(
        invitation_mail.send_invitation,
        invited_email=issued.invitation.email,
        organization_name=issued.organization_name,
        inviter_email=issued.inviter_email,
        inviter_display=issued.inviter_display,
        organization_role_display=issued.invitation.organization_role.value,
        grants=issued.grant_lines,
        accept_link=issued.accept_link,
        expires_at=issued.invitation.expires_at,
        invitation_id=issued.invitation.id,
    )

    return InvitationResponse.model_validate(issued.invitation)


@router.post(
    "/organizations/{organization_id}/invitations/{invitation_id}/resend",
    response_model=InvitationResponse,
    summary="Resend Organization Invitation",
)
async def resend_invitation(
    invitation_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    issued = organization_invitation_service.resend_invitation(
        db,
        organization=context.organization,
        invitation_id=invitation_id,
        actor_role=context.role,
    )

    background_tasks.add_task(
        invitation_mail.send_invitation,
        invited_email=issued.invitation.email,
        organization_name=issued.organization_name,
        inviter_email=issued.inviter_email,
        inviter_display=issued.inviter_display,
        organization_role_display=issued.invitation.organization_role.value,
        grants=issued.grant_lines,
        accept_link=issued.accept_link,
        expires_at=issued.invitation.expires_at,
        invitation_id=issued.invitation.id,
    )

    return InvitationResponse.model_validate(issued.invitation)


@router.post(
    "/organizations/{organization_id}/invitations/{invitation_id}/revoke",
    response_model=MessageResponse,
    summary="Revoke Organization Invitation",
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    result = organization_invitation_service.revoke_invitation(
        db,
        organization=context.organization,
        invitation_id=invitation_id,
        actor=context.user,
        actor_role=context.role,
    )

    background_tasks.add_task(
        invitation_mail.send_invitation_revoked,
        invited_email=result.invited_email,
        organization_name=result.organization_name,
        inviter_email=result.inviter_email,
        inviter_display=result.inviter_display,
        invitation_id=result.invitation_id,
    )
    return MessageResponse(message="Invitation revoked.")


@router.get(
    "/organizations/{organization_id}/invitations",
    response_model=OrganizationInvitationListResponse,
    summary="List Organization Invitations",
)
async def list_invitations(
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    invitations = organization_invitation_service.list_invitations(
        db, organization_id=context.organization_id
    )
    return OrganizationInvitationListResponse(
        items=[InvitationResponse.model_validate(i) for m in invitations for i in [m]],
        total=len(invitations),
    )


# ============================================================================
# Public / Recipient Lifecycle
# ============================================================================

@router.get(
    "/invitations/preview",
    response_model=InvitationPreviewResponse,
    summary="Preview Invitation",
)
async def preview_invitation(
    token: str,
    db: deps.DbSession,
) -> Any:
    return organization_invitation_service.preview_invitation(db, token=token)


@router.post(
    "/invitations/accept",
    response_model=OrganizationInvitationAcceptResponse,
    summary="Accept Invitation",
)
async def accept_invitation(
    payload: OrganizationInvitationTokenRequest,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    try:
        accepted = organization_invitation_service.accept_invitation(
            db, token=payload.token, actor=current_user
        )
        members_url = build_organization_members_link(accepted.organization_slug)
        background_tasks.add_task(
            invitation_mail.send_invitation_accepted,
            inviter_email=accepted.inviter_email,
            invited_email=accepted.invited_email,
            invited_display=accepted.invited_display,
            organization_name=accepted.organization_name,
            organization_role_display=accepted.organization_role.value,
            provisioned_grants=accepted.provisioned_grants,
            skipped_grant_count=accepted.skipped_grant_count,
            members_url=members_url,
            invitation_id=accepted.invitation_id,
        )
        return OrganizationInvitationAcceptResponse(
            invitation_id=accepted.invitation_id,
            organization_id=accepted.organization_id,
            organization_slug=accepted.organization_slug,
            organization_role=accepted.organization_role,
            provisioned_grants=[
                AcceptedGrantSummary(
                    workspace_name=g.workspace_name,
                    role=g.role_display,
                )
                for g in accepted.provisioned_grants
            ],
            skipped_grant_count=accepted.skipped_grant_count,
        )
    except SeatLimitExceededError:
        blocked = organization_invitation_service.describe_seat_blocked(db, token=payload.token)
        members_url = build_organization_members_link(blocked["organization_slug"])
        invitation_mail.send_invitation_seat_blocked(
            inviter_email=blocked["inviter_email"],
            invited_email=blocked["invited_email"],
            organization_name=blocked["organization_name"],
            seat_limit=blocked["seat_limit"],
            members_url=members_url,
            invitation_id=blocked["invitation_id"],
        )
        raise


@router.post(
    "/invitations/reject",
    response_model=MessageResponse,
    summary="Reject Invitation",
)
async def reject_invitation(
    payload: OrganizationInvitationTokenRequest,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    result = organization_invitation_service.reject_invitation(
        db, token=payload.token, actor=current_user
    )
    invitations_url = build_organization_invitations_link(result.organization_slug)
    background_tasks.add_task(
        invitation_mail.send_invitation_rejected,
        inviter_email=result.inviter_email,
        invited_email=result.invited_email,
        organization_name=result.organization_name,
        invitations_url=invitations_url,
        invitation_id=result.invitation_id,
    )
    return MessageResponse(message="Invitation declined.")


@router.get(
    "/me/invitations",
    response_model=MyPendingInvitationsResponse,
    summary="List My Pending Invitations",
)
async def list_my_invitations(
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    invitations = organization_invitation_service.list_invitations_for_user(
        db, user_id=current_user.id
    )
    items = []
    for inv in invitations:
        inviter = user_crud.get_user_by_id(db, user_id=inv.inviter_id)
        items.append(
            MyPendingInvitation(
                organization_name=inv.organization.name,
                organization_role=inv.organization_role,
                inviter_email=inviter.email if inviter else "",
                workspaces=[
                    WorkspacePreviewEntry(name=g.workspace.workspace_name, role=g.role)
                    for g in inv.grants
                    if g.workspace is not None
                ],
                expires_at=inv.expires_at,
            )
        )
    return MyPendingInvitationsResponse(items=items)