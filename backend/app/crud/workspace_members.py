from __future__ import annotations

import uuid
from sqlalchemy import select, delete
from sqlalchemy.orm import Session, selectinload

from app.models.workspace import WorkspaceMember, WorkspaceRole


# ============================================================================
# Read / Existence Operations
# ============================================================================

def get_membership(
    db: Session,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> WorkspaceMember | None:
    """
    Retrieves a single WorkspaceMember record for a user in a workspace.
    """
    return db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()


def get_workspace_members(
    db: Session,
    *,
    workspace_id: uuid.UUID,
) -> list[WorkspaceMember]:
    """
    Returns all member records belonging to a given workspace.
    Eagerly loads user records to avoid N+1 query problems.
    """
    return list(
        db.scalars(
            select(WorkspaceMember)
            .options(selectinload(WorkspaceMember.user))
            .where(WorkspaceMember.workspace_id == workspace_id)
        ).all()
    )


def get_user_memberships(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> list[WorkspaceMember]:
    """
    Returns all workspace memberships associated with a given user.
    Eagerly loads workspace records to avoid N+1 query problems.
    """
    return list(
        db.scalars(
            select(WorkspaceMember)
            .options(selectinload(WorkspaceMember.workspace))
            .where(WorkspaceMember.user_id == user_id)
        ).all()
    )


def membership_exists(
    db: Session,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> bool:
    """
    Returns True if a user's membership in a workspace exists.
    """
    return get_membership(db, user_id=user_id, workspace_id=workspace_id) is not None


# ============================================================================
# Write Operations
# ============================================================================

def create_membership(
    db: Session,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    role: WorkspaceRole = WorkspaceRole.VIEWER,
    is_active: bool = True,
) -> WorkspaceMember:
    """
    Creates a new WorkspaceMember record, preventing duplicate combinations.

    Participates in the caller's transaction context. Does not commit, rollback, or refresh.
    """
    # Prevent unique constraint failures by checking for existing memberships
    existing = get_membership(db, user_id=user_id, workspace_id=workspace_id)
    if existing:
        # Idempotently update attributes if they differ from target values
        if existing.role != role or existing.is_active != is_active:
            existing.role = role
            existing.is_active = is_active
            db.add(existing)
            db.flush()
        return existing

    member = WorkspaceMember(
        user_id=user_id,
        workspace_id=workspace_id,
        role=role,
        is_active=is_active,
    )
    db.add(member)
    db.flush()
    return member


def ensure_owner_membership(
    db: Session,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> WorkspaceMember:
    """
    Ensures an OWNER membership exists for a user and workspace.
    
    Participates in the caller's transaction context.
    """
    return create_membership(
        db,
        user_id=user_id,
        workspace_id=workspace_id,
        role=WorkspaceRole.OWNER,
        is_active=True,
    )


def remove_membership(
    db: Session,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> None:
    """
    Removes a user's membership from a workspace.
    
    Participates in the caller's transaction context.
    """
    db.execute(
        delete(WorkspaceMember).where(
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.workspace_id == workspace_id,
        )
    )
    db.flush()