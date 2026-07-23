from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.models.user import User
from app.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceResponse,
)

logger = logging.getLogger("app.api.v1.workspace")

router = APIRouter(
    tags=["Workspace"],
)


# ============================================================================
# Get
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