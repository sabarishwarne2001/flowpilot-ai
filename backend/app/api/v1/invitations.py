"""
Workspace invitation API router for FlowPilot AI.

Extracted from app/api/v1/workspace.py, which coupled invitations to the
single-membership workspace resolution that ARCH-01 removes.

Route shapes reflect what establishes context:

  /workspaces/{workspace_id}/invitations...   management, resolved by
                                              TenantContext
  /invitations/preview, /accept, /reject      token-addressed; a recipient has
                                              no tenant context yet, which is
                                              what the token establishes

Preview is public. Accept and reject are NOT. Before ARCH-01 both took only a
token, so any holder could act on the invitee's behalf, and reject in
particular gave a token holder a denial of service on the invitation. The token
identifies the invitation; the session identifies the actor.

Routes carry their full path. Register with no prefix.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from app.api import deps
from app.schemas.workspace_invitation import (
    WorkspaceInvitationAcceptResponse,
    WorkspaceInvitationCreate,
    WorkspaceInvitationPreviewResponse,
    WorkspaceInvitationResponse,
    WorkspaceInvitationTokenRequest,
)
from app.services import workspace_invitation as workspace_invitation_service
from app.services import workspace_member_service
from app.services.notification_service import notification_service

logger = logging.getLogger("app.api.v1.invitations")

router = APIRouter(tags=["Invitations"])


# ============================================================================
# Workspace-scoped management
# ============================================================================

@router.post(
    "/workspaces/{workspace_id}/invitations",
    response_model=WorkspaceInvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite Member To Workspace",
)
async def create_invitation(
    payload: WorkspaceInvitationCreate,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context=Depends(deps.RequireWorkspaceAdmin),
) -> Any:
    """
    Invites a user to this workspace.

    The service enforces that the invited role sits at or below the inviter's
    own authority, and that inviting at workspace ADMIN requires
    organization-level standing. Before ARCH-01 neither check existed at
    invitation time, so a Manager could invite an alternate address as OWNER
    and self-escalate.
    """
    access = workspace_member_service.resolve_workspace_access(
        db, workspace=context.workspace, user_id=context.user_id
    )
    issued = workspace_invitation_service.create_workspace_invitation(
        db,
        workspace=context.workspace,
        actor_access=access,
        email=payload.email,
        role=payload.role,
    )

    # plaintext_token is handed to the mailer and goes no further. It is absent
    # from WorkspaceInvitationResponse, so the API never returns the secret to
    # the inviter — only the invited mailbox receives it.
    background_tasks.add_task(
        notification_service.send_workspace_invitation,
        db=db,
        invitation=issued.invitation,
        workspace_name=context.workspace.workspace_name,
        plaintext_token=issued.plaintext_token,
    )
    return issued.invitation


@router.get(
    "/workspaces/{workspace_id}/invitations",
    response_model=list[WorkspaceInvitationResponse],
    summary="List Pending Workspace Invitations",
)
async def list_pending_invitations(
    db: deps.DbSession,
    context=Depends(deps.RequireWorkspaceAdmin),
) -> Any:
    """
    Returns pending invitations for this workspace.

    Expiry is evaluated lazily, so an entry may already be past its timestamp.
    The response carries expires_at and the client filters on it; ARCH-04's
    sweeper removes the discrepancy.
    """
    return workspace_invitation_service.list_pending_invitations(
        db, workspace=context.workspace
    )


@router.post(
    "/workspaces/{workspace_id}/invitations/{invitation_id}/revoke",
    response_model=WorkspaceInvitationResponse,
    summary="Revoke Workspace Invitation",
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    db: deps.DbSession,
    context=Depends(deps.RequireWorkspaceAdmin),
) -> Any:
    """
    Revokes a pending invitation.

    The service verifies the invitation belongs to the addressed workspace,
    so an actor authorized for one workspace cannot revoke an invitation
    belonging to another by supplying its identifier.
    """
    access = workspace_member_service.resolve_workspace_access(
        db, workspace=context.workspace, user_id=context.user_id
    )
    return workspace_invitation_service.revoke_workspace_invitation(
        db,
        workspace=context.workspace,
        actor_access=access,
        invitation_id=invitation_id,
    )


@router.post(
    "/workspaces/{workspace_id}/invitations/{invitation_id}/resend",
    response_model=WorkspaceInvitationResponse,
    summary="Resend Workspace Invitation",
)
async def resend_invitation(
    invitation_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context=Depends(deps.RequireWorkspaceAdmin),
) -> Any:
    """
    Reissues an invitation with a fresh token and expiry.

    Every authorization check is re-run, so a resend cannot preserve a role the
    actor is no longer permitted to grant.
    """
    access = workspace_member_service.resolve_workspace_access(
        db, workspace=context.workspace, user_id=context.user_id
    )
    issued = workspace_invitation_service.resend_workspace_invitation(
        db,
        workspace=context.workspace,
        actor_access=access,
        invitation_id=invitation_id,
    )

    background_tasks.add_task(
        notification_service.send_workspace_invitation,
        db=db,
        invitation=issued.invitation,
        workspace_name=context.workspace.workspace_name,
        plaintext_token=issued.plaintext_token,
    )
    return issued.invitation


# ============================================================================
# Token-addressed recipient actions
# ============================================================================

@router.get(
    "/invitations/preview",
    response_model=WorkspaceInvitationPreviewResponse,
    summary="Preview Invitation",
)
async def preview_invitation(
    db: deps.DbSession,
    token: str = Query(..., description="The secure invitation token."),
) -> Any:
    """
    Resolves an invitation for public display.

    Public by design: a recipient must see who invited them and to what before
    creating an account. Reads only, and returns no database identifiers.
    """
    return workspace_invitation_service.preview_workspace_invitation(
        db, token=token
    )


@router.post(
    "/invitations/accept",
    response_model=WorkspaceInvitationAcceptResponse,
    summary="Accept Invitation",
)
async def accept_invitation(
    payload: WorkspaceInvitationTokenRequest,
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    """
    Accepts an invitation on behalf of the authenticated actor.

    Authentication is the fix for the pre-ARCH-01 hole: the endpoint took only
    a token and granted membership to whoever owned the invited address, so any
    holder of a forwarded link could accept on the invitee's behalf.

    Provisions an organization seat and a workspace grant in one transaction,
    then returns the destination so the client can navigate straight in.
    """
    result = workspace_invitation_service.accept_workspace_invitation(
        db, token=payload.token, current_user=current_user
    )
    return WorkspaceInvitationAcceptResponse(
        invitation=WorkspaceInvitationResponse.model_validate(
            result.invitation
        ),
        organization_id=result.workspace.organization_id,
        organization_slug=result.workspace.organization.slug,
        workspace_id=result.workspace.id,
        workspace_slug=result.workspace.slug,
        workspace_role=result.workspace_role,
    )


@router.post(
    "/invitations/reject",
    response_model=WorkspaceInvitationResponse,
    summary="Decline Invitation",
)
async def reject_invitation(
    payload: WorkspaceInvitationTokenRequest,
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    """
    Declines an invitation on behalf of the authenticated actor.

    Authenticated for the same reason as acceptance. Unauthenticated rejection
    let any token holder burn an invitation the recipient had not yet seen.
    """
    return workspace_invitation_service.reject_workspace_invitation(
        db, token=payload.token, current_user=current_user
    )