"""
ARCH-04 invitation lifecycle endpoints.

Replaces the workspace-scoped invitation router. Registered in place of it —
see the router aggregation change in §3.6 — not alongside it; two routers
both claiming /invitations/preview would leave routing order, not intent,
deciding which one runs (§D7.2).

MAIL DISPATCH: every mutating endpoint below builds its result, dispatches the
corresponding app.services.invitation_mail function via BackgroundTasks, and
returns. That ordering is safe — FastAPI attaches a populated BackgroundTasks
instance to the Response it builds from a normal return.

ONE exception: the seat-blocked branch of accept_invitation. It has to notify
on FAILURE, and background_tasks.add_task() followed by raise silently drops
the task — the exception handler builds an unrelated Response that knows
nothing about it. That branch instead catches the exception, dispatches the
task, and returns a Response with background= set explicitly. See §D7.1.

Accept and reject require the authenticated actor, not just the token. Both use
CurrentUser rather than VerifiedUser.

Routes carry their full path. Register with no prefix.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from fastapi.responses import JSONResponse

from app.api import deps
from app.core.exception_handlers import ErrorResponse
from app.core.exceptions import SeatLimitExceededError
from app.core.links import (
    build_organization_invitations_link,
    build_organization_members_link,
)
from app.models.organization_invitation import InvitationStatus
from app.schemas.common import MessageResponse
from app.schemas.organization_invitation import (
    AcceptedGrantSummary,
    MyPendingInvitation,
    MyPendingInvitationsResponse,
    OrganizationInvitationAcceptResponse,
    OrganizationInvitationCreate,
    OrganizationInvitationListResponse,
    OrganizationInvitationPreviewResponse,
    OrganizationInvitationResponse,
    OrganizationInvitationTokenRequest,
    WorkspacePreviewEntry,
)
from app.services import organization_invitation_service
from app.services import invitation_mail

logger = logging.getLogger("app.api.v1.organization_invitations")

router = APIRouter(tags=["Invitations"])


# ============================================================================
# Organization-scoped management
# ============================================================================

@router.post(
    "/organizations/{organization_id}/invitations",
    response_model=OrganizationInvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite Member To Organization",
)
async def create_invitation(
    payload: OrganizationInvitationCreate,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    """
    Invites a user to the organization, with zero or more workspace grants.
    """
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
        organization_role_display=issued.invitation.organization_role.value,
        grants=issued.grant_lines,
        accept_link=issued.accept_link,
        expires_at=issued.invitation.expires_at,
        invitation_id=issued.invitation.id,
    )
    return OrganizationInvitationResponse.from_orm_invitation(issued.invitation)


@router.get(
    "/organizations/{organization_id}/invitations",
    response_model=OrganizationInvitationListResponse,
    summary="List Organization Invitations",
)
async def list_invitations(
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
    invitation_status: list[InvitationStatus] | None = Query(
        default=None,
        alias="status",
        description="Filter by status. Omit for all statuses.",
    ),
) -> Any:
    items = organization_invitation_service.list_invitations(
        db, organization_id=context.organization_id, statuses=invitation_status,
    )
    return OrganizationInvitationListResponse(
        items=[OrganizationInvitationResponse.from_orm_invitation(i) for i in items],
        total=len(items),
    )


@router.post(
    "/organizations/{organization_id}/invitations/{invitation_id}/resend",
    response_model=OrganizationInvitationResponse,
    summary="Resend Invitation",
)
async def resend_invitation(
    invitation_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    """
    Reissues a pending invitation with a fresh token (§D6.6).
    """
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
        organization_role_display=issued.invitation.organization_role.value,
        grants=issued.grant_lines,
        accept_link=issued.accept_link,
        expires_at=issued.invitation.expires_at,
        invitation_id=issued.invitation.id,
    )
    return OrganizationInvitationResponse.from_orm_invitation(issued.invitation)


@router.delete(
    "/organizations/{organization_id}/invitations/{invitation_id}",
    response_model=MessageResponse,
    summary="Revoke Invitation",
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    """Withdraws a pending invitation. Notifies the invitee, not the inviter (§B.7)."""
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
        invitation_id=result.invitation_id,
    )
    return MessageResponse(message="Invitation revoked.")


# ============================================================================
# Recipient actions
# ============================================================================

@router.get(
    "/invitations/preview",
    response_model=OrganizationInvitationPreviewResponse,
    summary="Preview Invitation Details",
    description=(
        "Resolves invitation context from its token, with no authentication. "
        "Lets a recipient see what they are joining before creating an account."
    ),
)
async def preview_invitation(token: str, db: deps.DbSession) -> Any:
    data = organization_invitation_service.preview_invitation(db, token=token)
    return OrganizationInvitationPreviewResponse(
        organization_name=data["organization_name"],
        inviter_email=data["inviter_email"],
        invited_email=data["invited_email"],
        organization_role=data["organization_role"],
        workspaces=[
            WorkspacePreviewEntry(name=w["name"], role=w["role"])
            for w in data["workspaces"]
        ],
        expires_at=data["expires_at"],
    )


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
    """
    See the module docstring and §D7.1 for why the seat-blocked branch is
    shaped differently from every other handler in this router.
    """
    try:
        result = organization_invitation_service.accept_invitation(
            db, token=payload.token, actor=current_user,
        )
    except SeatLimitExceededError as exc:
        notice = organization_invitation_service.describe_seat_blocked(
            db, token=payload.token
        )
        background_tasks.add_task(
            invitation_mail.send_invitation_seat_blocked,
            inviter_email=notice["inviter_email"],
            invited_email=notice["invited_email"],
            organization_name=notice["organization_name"],
            seat_limit=notice["seat_limit"],
            members_url=build_organization_members_link(notice["organization_slug"]),
            invitation_id=notice["invitation_id"],
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ErrorResponse(
                code="SEAT_LIMIT_EXCEEDED", message=str(exc),
            ).model_dump(),
            background=background_tasks,
        )

    background_tasks.add_task(
        invitation_mail.send_invitation_accepted,
        inviter_email=result.inviter_email,
        invited_email=result.invited_email,
        organization_name=result.organization_name,
        organization_role_display=result.organization_role.value,
        provisioned_grants=result.provisioned_grants,
        skipped_grant_count=result.skipped_grant_count,
        members_url=build_organization_members_link(result.organization_slug),
        invitation_id=result.invitation_id,
    )
    return OrganizationInvitationAcceptResponse(
        invitation_id=result.invitation_id,
        organization_id=result.organization_id,
        organization_slug=result.organization_slug,
        organization_role=result.organization_role,
        provisioned_grants=[
            AcceptedGrantSummary(workspace_name=g.workspace_name, role=g.role_display)
            for g in result.provisioned_grants
        ],
        skipped_grant_count=result.skipped_grant_count,
    )


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
        db, token=payload.token, actor=current_user,
    )

    background_tasks.add_task(
        invitation_mail.send_invitation_rejected,
        inviter_email=result.inviter_email,
        invited_email=result.invited_email,
        organization_name=result.organization_name,
        invitations_url=build_organization_invitations_link(result.organization_slug),
        invitation_id=result.invitation_id,
    )
    return MessageResponse(message="Invitation declined.")


# ============================================================================
# Personal view
# ============================================================================

@router.get(
    "/me/invitations",
    response_model=MyPendingInvitationsResponse,
    summary="List My Pending Invitations",
)
async def list_my_invitations(
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    """
    §D7.4 — informational only. No id, no token: the plaintext was never
    persisted, so this cannot offer a working accept link regardless of shape.
    The frontend page for this route should direct the user to their email.
    """
    invitations = organization_invitation_service.invitation_crud.list_pending_invitations_for_email(
        db, email=current_user.email,
    )
    return MyPendingInvitationsResponse(
        items=[
            MyPendingInvitation(
                organization_name=inv.organization.name,
                organization_role=inv.organization_role,
                inviter_email=inv.inviter.email,
                workspaces=[
                    WorkspacePreviewEntry(name=g.workspace.workspace_name, role=g.role)
                    for g in inv.grants
                    if g.workspace is not None
                ],
                expires_at=inv.expires_at,
            )
            for inv in invitations
        ]
    )