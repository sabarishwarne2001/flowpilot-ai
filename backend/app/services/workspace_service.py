"""
Business orchestration service for Workspaces within FlowPilot AI.

Coordinates workspace updates, onboarding creators, and membership setups 
while owning transaction boundaries.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app import crud
from app.core.exceptions import WorkspaceMemberError, WorkspaceNotFoundError
from app.core.transactions import commit_and_refresh, rollback_and_log_error
from app.crud import workspace_members as workspace_members_crud
from app.crud import workspace as workspace_crud
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole
from app.schemas.workspace import WorkspaceCreate, WorkspaceUpdate

logger = logging.getLogger("app.services.workspace_service")


def create_new_workspace(
    db: Session,
    *,
    user_id: uuid.UUID,
    workspace_in: WorkspaceCreate,
) -> Workspace:
    """
    Orchestrates the creation of a workspace and registers the creator as OWNER.
    """
    try:
        workspace = workspace_crud.create_workspace(
            db,
            workspace_name=workspace_in.workspace_name,
            company_name=workspace_in.company_name,
            timezone=workspace_in.timezone,
            language=workspace_in.language,
            currency=workspace_in.currency,
            date_format=workspace_in.date_format,
            company_logo_url=workspace_in.company_logo_url,
        )

        # Atomic companion creation of Owner membership
        workspace_members_crud.create_membership(
            db,
            user_id=user_id,
            workspace_id=workspace.id,
            role=WorkspaceRole.OWNER,
            is_active=True,
        )

        commit_and_refresh(db, workspace)
        return workspace
    except Exception as e:
        rollback_and_log_error(
            db,
            logger,
            "Failed to create workspace for user %s: %s",
            user_id,
            str(e),
            exc=e,
        )


def update_existing_workspace(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    workspace_in: WorkspaceUpdate,
) -> Workspace:
    """
    Orchestrates updating workspace details under transaction control.
    """
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise WorkspaceNotFoundError("Workspace not found.")

    try:
        update_data = workspace_in.model_dump(exclude_unset=True)
        updated = workspace_crud.update_workspace(
            db,
            workspace=workspace,
            update_data=update_data,
        )
        commit_and_refresh(db, updated)
        return updated
    except Exception as e:
        rollback_and_log_error(
            db,
            logger,
            "Failed to update workspace %s: %s",
            workspace_id,
            str(e),
            exc=e,
        )


def remove_workspace_member(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    member_user_id: uuid.UUID,
) -> None:
    """
    Orchestrates member removal while preventing the deletion of the last owner.
    """
    membership = workspace_members_crud.get_membership(
        db, user_id=member_user_id, workspace_id=workspace_id
    )
    if not membership:
        return

    # Hardening block: prevent deleting the last OWNER of a workspace
    if membership.role == WorkspaceRole.OWNER:
        owners = [
            m for m in workspace_members_crud.get_workspace_members(db, workspace_id=workspace_id)
            if m.role == WorkspaceRole.OWNER and m.is_active
        ]
        if len(owners) <= 1:
            raise WorkspaceMemberError(
                "Access Denied. A workspace must retain at least one active Owner."
            )

    try:
        workspace_members_crud.remove_membership(
            db, user_id=member_user_id, workspace_id=workspace_id
        )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(
            "Failed to remove member %s from workspace %s: %s",
            member_user_id,
            workspace_id,
            str(e),
        )
        raise