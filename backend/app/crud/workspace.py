from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole
from app.schemas.workspace import WorkspaceCreate
from app.schemas.workspace import WorkspaceUpdate


# ============================================================================
# Helpers
# ============================================================================

def _create_workspace_owner_membership(
    db: Session,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> WorkspaceMember | None:
    """
    Safely creates an OWNER membership record for a workspace if it does not exist.
    
    Adds the created record to the session. Must not commit, refresh, or rollback.
    """
    existing_member = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.role == WorkspaceRole.OWNER,
        )
    ).scalar_one_or_none()

    if not existing_member:
        member = WorkspaceMember(
            user_id=user_id,
            workspace_id=workspace_id,
            role=WorkspaceRole.OWNER,
            is_active=True,
        )
        db.add(member)
        return member

    return None


# ============================================================================
# Create
# ============================================================================

def create_workspace(
    db: Session,
    *,
    user_id: uuid.UUID,
    workspace_in: WorkspaceCreate,
) -> Workspace:
    """
    Creates a workspace for a user.
    """
    try:
        workspace = Workspace(
            user_id=user_id,
            **workspace_in.model_dump(),
        )

        db.add(workspace)
        db.flush()  # Populates workspace.id safely within the active transaction

        _create_workspace_owner_membership(
            db,
            user_id=user_id,
            workspace_id=workspace.id,
        )

        db.commit()
        db.refresh(workspace)
        return workspace
    except Exception:
        db.rollback()
        raise


# ============================================================================
# Read
# ============================================================================

def get_workspace(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> Workspace | None:
    """
    Returns the workspace belonging to the user, with eager loading of memberships.
    """
    return db.execute(
        select(Workspace)
        .options(selectinload(Workspace.members))
        .where(
            Workspace.user_id == user_id,
        )
    ).scalar_one_or_none()


def get_first_workspace(
    db: Session,
) -> Workspace | None:
    """
    Returns the first workspace, eagerly loading memberships.
    Used for public branding before login.
    """
    return db.execute(
        select(Workspace).options(selectinload(Workspace.members))
    ).scalar_one_or_none()


# ============================================================================
# Exists
# ============================================================================

def workspace_exists(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> bool:
    """
    Returns True if the user already owns a workspace.
    """
    return (
        get_workspace(
            db,
            user_id=user_id,
        )
        is not None
    )


# ============================================================================
# Update
# ============================================================================

BASE_DIR = Path(__file__).resolve().parents[2]


def delete_logo_file(logo_url: str | None) -> None:
    """
    Deletes a logo from disk.
    """
    if not logo_url:
        return

    if not logo_url.startswith("/uploads/logos/"):
        return

    file_path = BASE_DIR / logo_url.lstrip("/")

    if file_path.exists():
        file_path.unlink()


def update_workspace(
    db: Session,
    *,
    workspace: Workspace,
    workspace_in: WorkspaceUpdate,
) -> Workspace:
    """
    Updates an existing workspace.
    """
    try:
        update_data = workspace_in.model_dump(
            exclude_unset=True,
        )

        old_logo = workspace.company_logo_url
        new_logo = update_data.get("company_logo_url")

        if "company_logo_url" in update_data:
            if old_logo and old_logo != new_logo:
                delete_logo_file(old_logo)

        for field, value in update_data.items():
            setattr(
                workspace,
                field,
                value,
            )

        db.add(workspace)
        db.commit()
        db.refresh(workspace)
        return workspace
    except Exception:
        db.rollback()
        raise


# ============================================================================
# Delete
# ============================================================================

def delete_workspace(
    db: Session,
    *,
    workspace: Workspace,
) -> None:
    """
    Deletes the workspace.
    """
    try:
        db.delete(workspace)
        db.commit()
    except Exception:
        db.rollback()
        raise


# ============================================================================
# Upsert
# ============================================================================

def upsert_workspace(
    db: Session,
    *,
    user_id: uuid.UUID,
    workspace_in: WorkspaceCreate,
) -> Workspace:
    """
    Creates the workspace if it does not exist,
    otherwise updates the existing workspace.
    """
    workspace = get_workspace(
        db,
        user_id=user_id,
    )

    if workspace is None:
        return create_workspace(
            db,
            user_id=user_id,
            workspace_in=workspace_in,
        )

    # Re-use update_workspace helper to perform the update and commit transaction
    update = WorkspaceUpdate(
        **workspace_in.model_dump(),
    )
    updated_workspace = update_workspace(
        db,
        workspace=workspace,
        workspace_in=update,
    )

    # Re-verify and safely create OWNER membership if missing
    try:
        membership = _create_workspace_owner_membership(
            db,
            user_id=user_id,
            workspace_id=updated_workspace.id,
        )
        if membership:
            db.commit()
            db.refresh(updated_workspace)
    except Exception:
        db.rollback()
        raise

    return updated_workspace