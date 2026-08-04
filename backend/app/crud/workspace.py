"""
Database CRUD repository for FlowPilot AI workspaces.

Responsible only for persistence operations.
Transactions and business rules are delegated to the workspace service layer.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workspace import Workspace, WorkspaceMember


# ============================================================================
# Create
# ============================================================================

def create_workspace(
    db: Session,
    *,
    workspace_name: str,
    company_name: str,
    timezone: str = "UTC",
    language: str = "en",
    currency: str = "USD",
    date_format: str = "YYYY-MM-DD",
    company_logo_url: str | None = None,
) -> Workspace:
    """
    Creates a workspace record. Participates in caller's transaction context.
    """
    workspace = Workspace(
        workspace_name=workspace_name,
        company_name=company_name,
        timezone=timezone,
        language=language,
        currency=currency,
        date_format=date_format,
        company_logo_url=company_logo_url,
        is_active=True,
        # Maintain legacy user_id column fallback with a placeholder (UUIDv4)
        user_id=uuid.uuid4(),
    )
    db.add(workspace)
    db.flush()
    return workspace


# ============================================================================
# Read / Existence
# ============================================================================

def get_workspace(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> Workspace | None:
    """
    Retrieves the active workspace for a given user through their WorkspaceMember relationship.
    
    Migrated away from legacy Workspace.user_id dependency.
    """
    stmt = select(WorkspaceMember).where(
        WorkspaceMember.user_id == user_id,
        WorkspaceMember.is_active == True,
    )
    member = db.execute(stmt).scalar_one_or_none()
    if member:
        return member.workspace
    return None


def get_first_workspace(
    db: Session,
) -> Workspace | None:
    """
    Returns the first workspace in the database.
    """
    return db.execute(
        select(Workspace)
    ).scalar_one_or_none()


def workspace_exists(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> bool:
    """
    Returns True if the user already holds active membership in a workspace.
    """
    return get_workspace(db, user_id=user_id) is not None


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
    update_data: dict,
) -> Workspace:
    """
    Updates an existing workspace record. Participates in caller's transaction.
    """
    old_logo = workspace.company_logo_url
    new_logo = update_data.get("company_logo_url")

    if "company_logo_url" in update_data:
        if old_logo and old_logo != new_logo:
            delete_logo_file(old_logo)

    for field, value in update_data.items():
        setattr(workspace, field, value)

    db.add(workspace)
    db.flush()
    return workspace


# ============================================================================
# Delete
# ============================================================================

def delete_workspace(
    db: Session,
    *,
    workspace: Workspace,
) -> None:
    """
    Deletes the workspace record. Participates in caller's transaction context.
    """
    db.delete(workspace)
    db.flush()