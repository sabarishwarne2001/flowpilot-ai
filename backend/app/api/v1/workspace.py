from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.core import workspace_permissions
from app.crud import workspace_members
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.workspace import WorkspaceCreate, WorkspaceResponse
from app.schemas.workspace_member import WorkspaceMemberResponse
from app.schemas.workspace_invitation import (
    WorkspaceInvitationCreate,
    WorkspaceInvitationResponse,
    WorkspaceInvitationListResponse,
    WorkspaceInvitationTokenRequest,
)
from app.services import workspace_invitation as workspace_invitation_service

logger = logging.getLogger("app.api.v1.workspace")

router = APIRouter(
    tags=["Workspace"],
)


def _get_user_workspace(db: Session, user_id: uuid.UUID) -> Workspace:
    """
    Private router helper to resolve the active workspace using legacy ownership.
    Reduces duplicated 404 boilerplate across endpoints until Sprint 3.
    """
    workspace = crud.get_workspace(db, user_id=user_id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not configured.",
        )
    return workspace


# ============================================================================
# Get Workspace
# ============================================================================

@router.get(
    "",
    response_model=WorkspaceResponse,
    summary="Get Active Workspace",
    description="Retrieves active workspace details matching the authenticated user profile using legacy ownership lookup constraints."
)
async def get_workspace(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> WorkspaceResponse:
    return _get_user_workspace(db, current_user.id)


@router.get(
    "/public",
    response_model=WorkspaceResponse,
    summary="Get Public Workspace Branding",
    description="Returns public landing-page branding and logo URLs. This route intentionally does not require authentication."
)
async def get_public_workspace(
    db: Session = Depends(deps.get_db),
) -> WorkspaceResponse:
    workspace = crud.get_first_workspace(db)

    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not configured.",
        )

    return workspace


# ============================================================================
# Create / Update
# ============================================================================

@router.put(
    "",
    response_model=WorkspaceResponse,
    summary="Create or Update Workspace",
    description="Idempotently creates or updates the active workspace. Automatically synchronizes database many-to-many OWNER memberships."
)
async def upsert_workspace(
    workspace_in: WorkspaceCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> WorkspaceResponse:
    workspace = crud.upsert_workspace(
        db,
        user_id=current_user.id,
        workspace_in=workspace_in,
    )
    logger.info("Updated workspace for user %s.", current_user.id)
    return workspace


# ============================================================================
# Memberships (Read-Only API)
# ============================================================================

@router.get(
    "/members",
    response_model=list[WorkspaceMemberResponse],
    summary="List Workspace Members",
    description="Retrieves a complete list of workspace member accounts. The requesting actor must possess at least active Viewer authorization."
)
async def list_workspace_members(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> list[WorkspaceMemberResponse]:
    workspace = _get_user_workspace(db, current_user.id)

    membership = workspace_members.get_membership(
        db, user_id=current_user.id, workspace_id=workspace.id
    )
    if not membership or not membership.is_active or not workspace_permissions.is_workspace_viewer(membership.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Active membership required.",
        )

    return workspace_members.get_workspace_members(db, workspace_id=workspace.id)


@router.get(
    "/members/me",
    response_model=WorkspaceMemberResponse,
    summary="Get My Workspace Membership",
    description="Retrieves current membership details and role assignments of the active user profile inside the workspace."
)
async def get_my_membership(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> WorkspaceMemberResponse:
    workspace = _get_user_workspace(db, current_user.id)

    membership = workspace_members.get_membership(
        db, user_id=current_user.id, workspace_id=workspace.id
    )
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Membership record not found.",
        )

    return membership


# ============================================================================
# Workspace Invitations
# ============================================================================

@router.post(
    "/invite",
    response_model=WorkspaceInvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite New Member",
    description="Sends a secure email invitation to join the active workspace under a designated role. Restricted to Owners and Managers."
)
async def invite_user(
    invitation_in: WorkspaceInvitationCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> WorkspaceInvitationResponse:
    workspace = _get_user_workspace(db, current_user.id)

    # Delegate entirely to the service layer orchestration
    return workspace_invitation_service.create_workspace_invitation(
        db,
        workspace_id=workspace.id,
        inviter_id=current_user.id,
        email=invitation_in.email,
        role=invitation_in.role,
    )


@router.get(
    "/invitations",
    response_model=WorkspaceInvitationListResponse,
    summary="List Workspace Invitations",
    description="Retrieves a complete list of invitations (all statuses) associated with the current active workspace."
)
async def list_invitations(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> WorkspaceInvitationListResponse:
    workspace = _get_user_workspace(db, current_user.id)

    invitations = workspace_invitation_service.get_workspace_invitations(
        db,
        workspace_id=workspace.id,
        actor_id=current_user.id,
    )
    return WorkspaceInvitationListResponse(invitations=invitations)


@router.get(
    "/invitations/pending",
    response_model=WorkspaceInvitationListResponse,
    summary="List Pending Workspace Invitations",
    description="Retrieves all active PENDING invitations associated with the current active workspace."
)
async def list_pending_invitations(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> WorkspaceInvitationListResponse:
    workspace = _get_user_workspace(db, current_user.id)

    invitations = workspace_invitation_service.get_pending_workspace_invitations(
        db,
        workspace_id=workspace.id,
        actor_id=current_user.id,
    )
    return WorkspaceInvitationListResponse(invitations=invitations)


@router.post(
    "/invitations/{invitation_id}/revoke",
    response_model=WorkspaceInvitationResponse,
    summary="Revoke Workspace Invitation",
    description="Cancels and revokes an active pending invitation. The recipient will no longer be able to use the associated secure token."
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> WorkspaceInvitationResponse:
    return workspace_invitation_service.revoke_workspace_invitation(
        db,
        invitation_id=invitation_id,
        actor_id=current_user.id,
    )


@router.post(
    "/invitations/{invitation_id}/resend",
    response_model=WorkspaceInvitationResponse,
    summary="Resend Workspace Invitation",
    description="Resends a pending or expired invitation by revoking the old record and issuing a fresh token with updated expiry constraints."
)
async def resend_invitation(
    invitation_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> WorkspaceInvitationResponse:
    return workspace_invitation_service.resend_workspace_invitation(
        db,
        invitation_id=invitation_id,
        actor_id=current_user.id,
    )


@router.post(
    "/invitations/accept",
    response_model=WorkspaceInvitationResponse,
    summary="Accept Workspace Invitation",
    description="Validates a secure URL-safe token, associates the recipient email profile, and registers a new active workspace member."
)
async def accept_invitation(
    request: WorkspaceInvitationTokenRequest,
    db: Session = Depends(deps.get_db),
) -> WorkspaceInvitationResponse:
    return workspace_invitation_service.accept_workspace_invitation(
        db,
        token=request.token,
    )


@router.post(
    "/invitations/reject",
    response_model=WorkspaceInvitationResponse,
    summary="Reject Workspace Invitation",
    description="Rejects a workspace membership invitation using its secure token, transitioning status to REJECTED."
)
async def reject_invitation(
    request: WorkspaceInvitationTokenRequest,
    db: Session = Depends(deps.get_db),
) -> WorkspaceInvitationResponse:
    return workspace_invitation_service.reject_workspace_invitation(
        db,
        token=request.token,
    )