from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workspace import WorkspaceRole
from app.models.workspace_invitation import WorkspaceInvitation, InvitationStatus


# ============================================================================
# Read / Existence Operations
# ============================================================================

def get_invitation_by_id(
    db: Session,
    invitation_id: uuid.UUID,
) -> WorkspaceInvitation | None:
    """
    Retrieves a workspace invitation by its primary key.
    """
    return db.execute(
        select(WorkspaceInvitation).where(WorkspaceInvitation.id == invitation_id)
    ).scalar_one_or_none()


def get_invitation_by_token(
    db: Session,
    token: str,
) -> WorkspaceInvitation | None:
    """
    Retrieves a workspace invitation by its unique secure token.
    """
    return db.execute(
        select(WorkspaceInvitation).where(WorkspaceInvitation.token == token)
    ).scalar_one_or_none()


def get_pending_invitation(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    email: str,
) -> WorkspaceInvitation | None:
    """
    Retrieves an active PENDING invitation for a given email and workspace combination.
    """
    normalized_email = email.strip().lower()
    return db.execute(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.email == normalized_email,
            WorkspaceInvitation.status == InvitationStatus.PENDING,
        )
    ).scalar_one_or_none()


def list_workspace_invitations(
    db: Session,
    *,
    workspace_id: uuid.UUID,
) -> list[WorkspaceInvitation]:
    """
    Lists all invitations (regardless of status) associated with a given workspace.
    """
    return list(
        db.scalars(
            select(WorkspaceInvitation)
            .where(WorkspaceInvitation.workspace_id == workspace_id)
            .order_by(WorkspaceInvitation.created_at.desc())
        ).all()
    )


def list_pending_workspace_invitations(
    db: Session,
    *,
    workspace_id: uuid.UUID,
) -> list[WorkspaceInvitation]:
    """
    Lists all active PENDING invitations associated with a given workspace.
    """
    return list(
        db.scalars(
            select(WorkspaceInvitation)
            .where(
                WorkspaceInvitation.workspace_id == workspace_id,
                WorkspaceInvitation.status == InvitationStatus.PENDING,
            )
            .order_by(WorkspaceInvitation.created_at.desc())
        ).all()
    )


def invitation_exists(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    email: str,
) -> bool:
    """
    Checks if an active pending invitation exists for the given workspace and email.
    """
    return get_pending_invitation(db, workspace_id=workspace_id, email=email) is not None


# ============================================================================
# Write Operations
# ============================================================================

def create_invitation(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    inviter_id: uuid.UUID,
    email: str,
    role: WorkspaceRole,
    token: str,
    expires_at: datetime,
) -> WorkspaceInvitation:
    """
    Creates a new WorkspaceInvitation record.
    
    Participates in the caller's transaction context. Does not commit or rollback.
    """
    # Defensive lookup to prevent uq_pending_invitation constraint violations
    normalized_email = email.strip().lower()
    existing = get_pending_invitation(db, workspace_id=workspace_id, email=normalized_email)
    if existing:
        raise ValueError("A pending invitation already exists for this email in this workspace.")

    invitation = WorkspaceInvitation(
        workspace_id=workspace_id,
        inviter_id=inviter_id,
        email=normalized_email,
        role=role,
        token=token,
        expires_at=expires_at,
        status=InvitationStatus.PENDING,
    )
    db.add(invitation)
    db.flush()
    return invitation


def mark_invitation_accepted(
    db: Session,
    *,
    invitation: WorkspaceInvitation,
) -> WorkspaceInvitation:
    """
    Updates the invitation status to ACCEPTED and records the timestamp.
    
    Participates in the caller's transaction context. Idempotent: if the
    invitation is not currently PENDING, it is returned unchanged.
    """
    if invitation.status != InvitationStatus.PENDING:
        return invitation

    invitation.status = InvitationStatus.ACCEPTED
    invitation.accepted_at = datetime.now(timezone.utc)
    db.add(invitation)
    db.flush()
    return invitation


def mark_invitation_rejected(
    db: Session,
    *,
    invitation: WorkspaceInvitation,
) -> WorkspaceInvitation:
    """
    Updates the invitation status to REJECTED and records the timestamp.
    
    Participates in the caller's transaction context. Idempotent: if the
    invitation is not currently PENDING, it is returned unchanged.
    """
    if invitation.status != InvitationStatus.PENDING:
        return invitation

    invitation.status = InvitationStatus.REJECTED
    invitation.rejected_at = datetime.now(timezone.utc)
    db.add(invitation)
    db.flush()
    return invitation


def mark_invitation_revoked(
    db: Session,
    *,
    invitation: WorkspaceInvitation,
) -> WorkspaceInvitation:
    """
    Updates the invitation status to REVOKED and records the timestamp.
    
    Participates in the caller's transaction context. Idempotent: if the
    invitation is not currently PENDING, it is returned unchanged.
    """
    if invitation.status != InvitationStatus.PENDING:
        return invitation

    invitation.status = InvitationStatus.REVOKED
    invitation.revoked_at = datetime.now(timezone.utc)
    db.add(invitation)
    db.flush()
    return invitation


def mark_invitation_expired(
    db: Session,
    *,
    invitation: WorkspaceInvitation,
) -> WorkspaceInvitation:
    """
    Updates the invitation status to EXPIRED.
    
    Participates in the caller's transaction context. Idempotent: if the
    invitation is not currently PENDING, it is returned unchanged.
    """
    if invitation.status != InvitationStatus.PENDING:
        return invitation

    invitation.status = InvitationStatus.EXPIRED
    db.add(invitation)
    db.flush()
    return invitation


def is_invitation_expired(
    invitation: WorkspaceInvitation,
) -> bool:
    """
    Returns True if the invitation's expiry timestamp has passed.
    """
    return invitation.expires_at <= datetime.now(timezone.utc)


def delete_invitation(
    db: Session,
    *,
    invitation: WorkspaceInvitation,
) -> None:
    """
    Deletes an invitation record from the database.
    
    Participates in the caller's transaction context.
    """
    db.delete(invitation)


# ============================================================================
# Aliases
# ============================================================================

# Alias to satisfy the runtime contract verification script
list_pending_invitations = list_pending_workspace_invitations