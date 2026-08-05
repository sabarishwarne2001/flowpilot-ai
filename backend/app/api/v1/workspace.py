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
)
from app.services import workspace_invitation as workspace_invitation_service
from app.services import workspace_service
from app.services.notification_service import notification_service
from app.core.exceptions import WorkspaceMemberError

logger = logging.getLogger("app.api.v1.workspace")

router = APIRouter(
    tags=["Workspace"],
)


def _get_user_workspace_member(db: Session, user_id: uuid.UUID) -> WorkspaceMember:
    """
    Resolves active workspace membership directly from user context.
    """
    stmt = select(WorkspaceMember).where(
        WorkspaceMember.user_id == user_id,
        WorkspaceMember.is_active == True,
    )
    membership = db.execute(stmt).scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not configured. Please complete onboarding.",
        )
    return membership


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

    # Secure modifications strictly to Owner & Manager
    membership = _get_user_workspace_member(db, current_user.id)
    if membership.role not in [WorkspaceRole.OWNER, WorkspaceRole.MANAGER]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied. Workspace administration is restricted.",
        )

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
    dependencies=[Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER, WorkspaceRole.CONTRIBUTOR, WorkspaceRole.VIEWER]))]
)
async def list_workspace_members(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> list[WorkspaceMemberResponse]:
    membership = _get_user_workspace_member(db, current_user.id)
    return crud.get_workspace_members(db, workspace_id=membership.workspace_id)


@router.delete(
    "/members/{member_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove Workspace Member",
    description="Deletes a workspace member. Enforces Owner/Manager privilege controls, role hierarchy, and prevents deleting the last active Owner."
)
async def remove_member(
    member_user_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> None:
    membership = _get_user_workspace_member(db, current_user.id)
    
    # 1. Self-removal (Leaving the workspace)
    if member_user_id == current_user.id:
        target_membership = membership
    else:
        # 2. Administrative removal: Only Owners or Managers can remove other members
        if membership.role not in [WorkspaceRole.OWNER, WorkspaceRole.MANAGER]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. Insufficient role permissions."
            )
        target_membership = crud.workspace_members_crud.get_membership(
            db, user_id=member_user_id, workspace_id=membership.workspace_id
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
            m for m in crud.workspace_members_crud.get_workspace_members(db, workspace_id=membership.workspace_id)
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


# ============================================================================
# Workspace Invitations (RBAC Protections)
# ============================================================================

@router.get(
    "/invitations/preview",
    summary="Preview Invitation Details",
    description="Resolves and returns invitation context securely based on its token. This endpoint is public."
)
async def preview_invitation(
    token: str,
    db: Session = Depends(deps.get_db),
) -> dict:
    return workspace_invitation_service.preview_workspace_invitation(db, token=token)


@router.post(
    "/invite",
    response_model=WorkspaceInvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite New Member",
    dependencies=[Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER]))]
)
async def invite_user(
    invitation_in: WorkspaceInvitationCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> WorkspaceInvitationResponse:
    membership = _get_user_workspace_member(db, current_user.id)
    workspace = membership.workspace

    invitation = workspace_invitation_service.create_workspace_invitation(
        db,
        workspace_id=workspace.id,
        inviter_id=current_user.id,
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


@router.post(
    "/invitations/{invitation_id}/revoke",
    response_model=WorkspaceInvitationResponse,
    summary="Revoke Workspace Invitation",
    dependencies=[Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER]))]
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
    dependencies=[Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER]))]
)
async def resend_invitation(
    invitation_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> WorkspaceInvitationResponse:
    invitation = workspace_invitation_service.resend_workspace_invitation(
        db,
        invitation_id=invitation_id,
        actor_id=current_user.id,
    )

    membership = _get_user_workspace_member(db, current_user.id)
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