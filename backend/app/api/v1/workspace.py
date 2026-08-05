from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Response
from sqlalchemy.orm import Session
from sqlalchemy import select

from app import crud
from app.api import deps
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceRole, WorkspaceMember
from app.models.workspace_invitation import WorkspaceInvitation, InvitationStatus
from app.schemas.workspace import WorkspaceCreate, WorkspaceResponse, WorkspaceUpdate
from app.schemas.workspace_member import WorkspaceMemberResponse
from app.schemas.workspace_invitation import (
    WorkspaceInvitationCreate,
    WorkspaceInvitationResponse,
    WorkspaceInvitationListResponse,
    WorkspaceInvitationTokenRequest,
    WorkspaceInvitationPreviewResponse,
)
from app.services import workspace_invitation as workspace_invitation_service
from app.services import workspace_service
from app.services.notification_service import notification_service
from app.core.exceptions import WorkspaceMemberError

logger = logging.getLogger("app.api.v1.workspace")

router = APIRouter(
    tags=["Workspace"],
)


# ============================================================================
# Get Workspace / Onboarding Endpoint
# ============================================================================

@router.get(
    "",
    response_model=WorkspaceResponse,
    summary="Get Active Workspace",
    description="Retrieves the active workspace. Returns 204 No Content if no workspace is initialized, enabling non-blocking onboarding."
)
async def get_workspace(
    response: Response,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    workspace = crud.get_workspace(db, user_id=current_user.id)
    if workspace is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return workspace


@router.get(
    "/public",
    response_model=WorkspaceResponse,
    summary="Get Public Workspace Branding",
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
# Create / Update (Transactional Services & RBAC Guards)
# ============================================================================

@router.put(
    "",
    response_model=WorkspaceResponse,
    summary="Create or Update Workspace Settings",
    description="Transactional onboarding and updating endpoint. Enforces that only OWNER/MANAGER may modify settings."
)
async def upsert_workspace(
    workspace_in: WorkspaceCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> WorkspaceResponse:
    workspace = crud.get_workspace(db, user_id=current_user.id)

    if workspace is None:
        return workspace_service.create_new_workspace(
            db,
            user_id=current_user.id,
            workspace_in=workspace_in,
        )

    # Secure modifications: Inject validated OWNER/MANAGER role dependency directly
    _ = deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER])(db, current_user)

    update = WorkspaceUpdate(**workspace_in.model_dump())
    return workspace_service.update_existing_workspace(
        db,
        workspace_id=workspace.id,
        workspace_in=update,
    )


# ============================================================================
# Memberships (RBAC Protections)
# ============================================================================

@router.get(
    "/members",
    response_model=list[WorkspaceMemberResponse],
    summary="List Workspace Members",
)
async def list_workspace_members(
    db: Session = Depends(deps.get_db),
    membership: WorkspaceMember = Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER, WorkspaceRole.CONTRIBUTOR, WorkspaceRole.VIEWER]))
) -> list[WorkspaceMemberResponse]:
    return crud.get_workspace_members(db, workspace_id=membership.workspace_id)


@router.get(
    "/members/me",
    response_model=WorkspaceMemberResponse,
    summary="Get Current User Membership",
)
async def get_my_membership(
    membership: WorkspaceMember = Depends(
        deps.RequireRole(
            [
                WorkspaceRole.OWNER,
                WorkspaceRole.MANAGER,
                WorkspaceRole.CONTRIBUTOR,
                WorkspaceRole.VIEWER,
            ]
        )
    ),
) -> WorkspaceMemberResponse:
    return membership


@router.delete(
    "/members/{member_user_id}",
    status_code=status.HTTP_200_OK,
    summary="Remove Workspace Member",
    description="Removes a member from the workspace or allows a member to leave if they are not the last active OWNER."
)
async def remove_member(
    member_user_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> None:
    # 1. Resolve actor's membership
    stmt = select(WorkspaceMember).where(
        WorkspaceMember.user_id == current_user.id,
        WorkspaceMember.is_active == True,
    )
    membership = db.execute(stmt).scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not configured. Please complete onboarding.",
        )

    # 2. Self-removal (Leaving the workspace)
    if member_user_id == current_user.id:
        target_membership = membership
    else:
        # 3. Administrative removal: Only Owners or Managers can remove other members
        if membership.role not in [WorkspaceRole.OWNER, WorkspaceRole.MANAGER]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. Insufficient role permissions."
            )
        target_membership = crud.get_membership(
            db,
            user_id=member_user_id,
            workspace_id=membership.workspace_id,
        )
        if not target_membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target member not found in this workspace."
            )
        
        # Enforce role hierarchy: Managers cannot remove Owners or other Managers
        if membership.role == WorkspaceRole.MANAGER and target_membership.role in [WorkspaceRole.OWNER, WorkspaceRole.MANAGER]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. Managers cannot remove Owners or other Managers."
            )

    # If the target being removed is an OWNER, verify they are not the last active Owner
    if target_membership.role == WorkspaceRole.OWNER:
        active_owners = [
            m
            for m in crud.get_workspace_members(
                db,
                workspace_id=membership.workspace_id,
            )
            if m.role == WorkspaceRole.OWNER and m.is_active
        ]
        if len(active_owners) <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the last active Owner. You must promote another member to Owner first."
            )

    try:
        workspace_service.remove_workspace_member(
            db,
            workspace_id=membership.workspace_id,
            member_user_id=member_user_id,
        )
    except WorkspaceMemberError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    return {
        "message": "Workspace member removed successfully."
    }


# ============================================================================
# Workspace Invitations (RBAC Protections)
# ============================================================================

@router.get(
    "/invitations/preview",
    response_model=WorkspaceInvitationPreviewResponse,
    summary="Preview Invitation Details",
    description="Resolves and returns invitation context securely based on its token. This endpoint is public."
)
async def preview_invitation(
    token: str,
    db: Session = Depends(deps.get_db),
) -> Any:
    return workspace_invitation_service.preview_workspace_invitation(db, token=token)


@router.post(
    "/invite",
    response_model=WorkspaceInvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite New Member",
)
async def invite_user(
    invitation_in: WorkspaceInvitationCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
    membership: WorkspaceMember = Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER])),
) -> WorkspaceInvitationResponse:
    workspace = membership.workspace

    invitation = workspace_invitation_service.create_workspace_invitation(
        db,
        workspace_id=workspace.id,
        inviter_id=membership.user_id,
        email=invitation_in.email,
        role=invitation_in.role,
    )

    background_tasks.add_task(
        notification_service.send_workspace_invitation,
        db=db,
        invitation=invitation,
        workspace_name=workspace.workspace_name,
    )
    return invitation


@router.get(
    "/invitations/pending",
    response_model=list[WorkspaceInvitationResponse],
    summary="List Pending Workspace Invitations",
)
async def list_pending_invitations(
    db: Session = Depends(deps.get_db),
    membership: WorkspaceMember = Depends(
        deps.RequireRole(
            [
                WorkspaceRole.OWNER,
                WorkspaceRole.MANAGER,
            ]
        )
    ),
) -> list[WorkspaceInvitationResponse]:
    return crud.list_pending_workspace_invitations(
        db,
        workspace_id=membership.workspace_id,
    )


@router.post(
    "/invitations/{invitation_id}/revoke",
    response_model=WorkspaceInvitationResponse,
    summary="Revoke Workspace Invitation",
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    membership: WorkspaceMember = Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER])),
) -> WorkspaceInvitationResponse:
    return workspace_invitation_service.revoke_workspace_invitation(
        db,
        invitation_id=invitation_id,
        actor_id=membership.user_id,
    )


@router.post(
    "/invitations/{invitation_id}/resend",
    response_model=WorkspaceInvitationResponse,
    summary="Resend Workspace Invitation",
)
async def resend_invitation(
    invitation_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
    membership: WorkspaceMember = Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER])),
) -> WorkspaceInvitationResponse:
    invitation = workspace_invitation_service.resend_workspace_invitation(
        db,
        invitation_id=invitation_id,
        actor_id=membership.user_id,
    )

    background_tasks.add_task(
        notification_service.send_workspace_invitation,
        db=db,
        invitation=invitation,
        workspace_name=membership.workspace.workspace_name,
    )
    return invitation


@router.post(
    "/invitations/accept",
    response_model=WorkspaceInvitationResponse,
    summary="Accept Workspace Invitation",
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
)
async def reject_invitation(
    request: WorkspaceInvitationTokenRequest,
    db: Session = Depends(deps.get_db),
) -> WorkspaceInvitationResponse:
    return workspace_invitation_service.reject_workspace_invitation(
        db,
        token=request.token,
    )