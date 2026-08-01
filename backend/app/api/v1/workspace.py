from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.core import workspace_permissions
from app.crud import workspace_members
from app.models.user import User
from app.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceResponse,
)
from app.schemas.workspace_member import WorkspaceMemberResponse

logger = logging.getLogger("app.api.v1.workspace")

router = APIRouter(
    tags=["Workspace"],
)


# ============================================================================
# Get Workspace
# ============================================================================

@router.get(
    "",
    response_model=WorkspaceResponse,
    summary="Get Workspace",
)
async def get_workspace(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
) -> WorkspaceResponse:
    """
    Returns the authenticated user's workspace.
    
    Aligned with the Sprint 1 Expand phase: legacy user_id ownership lookup 
    remains active while database-level memberships are maintained concurrently.
    """

    workspace = crud.get_workspace(
        db,
        user_id=current_user.id,
    )

    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not configured.",
        )

    return workspace


@router.get(
    "/public",
    response_model=WorkspaceResponse,
    summary="Get Public Workspace",
)
async def get_public_workspace(
    db: Session = Depends(deps.get_db),
) -> WorkspaceResponse:
    """
    Returns public workspace branding.
    Does not require authentication.
    """

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
)
async def upsert_workspace(
    workspace_in: WorkspaceCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
) -> WorkspaceResponse:
    """
    Creates or updates the user's workspace.
    
    Aligned with the Sprint 1 Expand phase: legacy ownership fields and 
    associated WorkspaceMember OWNER records are synchronized atomically.
    """

    workspace = crud.upsert_workspace(
        db,
        user_id=current_user.id,
        workspace_in=workspace_in,
    )

    logger.info(
        "Updated workspace for user %s.",
        current_user.id,
    )

    return workspace


# ============================================================================
# Memberships (Read-Only API)
# ============================================================================

@router.get(
    "/members",
    response_model=list[WorkspaceMemberResponse],
    summary="List Workspace Members",
)
async def list_workspace_members(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> list[WorkspaceMemberResponse]:
    """
    Returns all members belonging to the authenticated user's workspace.
    
    Validates that the requesting user holds an active membership record 
    with at least Viewer privileges in the current workspace.
    """
    # 1. Resolve active workspace using legacy ownership context
    workspace = crud.get_workspace(db, user_id=current_user.id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not configured.",
        )

    # 2. Assert active membership using stateless permissions helpers
    membership = workspace_members.get_membership(
        db, user_id=current_user.id, workspace_id=workspace.id
    )
    if not membership or not membership.is_active or not workspace_permissions.is_workspace_viewer(membership.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Active membership required.",
        )

    # 3. Retrieve and return workspace members (eagerly loading user entities)
    return workspace_members.get_workspace_members(db, workspace_id=workspace.id)


@router.get(
    "/members/me",
    response_model=WorkspaceMemberResponse,
    summary="Get My Workspace Membership",
)
async def get_my_membership(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> WorkspaceMemberResponse:
    """
    Returns the authenticated user's current workspace membership details.
    """
    # 1. Resolve active workspace using legacy ownership context
    workspace = crud.get_workspace(db, user_id=current_user.id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not configured.",
        )

    # 2. Retrieve membership details
    membership = workspace_members.get_membership(
        db, user_id=current_user.id, workspace_id=workspace.id
    )
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Membership record not found.",
        )

    return membership