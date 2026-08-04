from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

from app.core import workspace_permissions
from app.core.exceptions import (
    InvitationAlreadyExistsError,
    InvitationAlreadyMemberError,
    InvitationAlreadyProcessedError,
    InvitationError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationPermissionDeniedError,
    InvalidInvitationTokenError,
)
from app.core.tokens import generate_secure_token
from app.core.transactions import (
    commit_and_refresh,
    rollback_and_log_error,
)
from app.crud import user as user_crud
from app.crud import workspace_invitation as workspace_invitation_crud
from app.crud import workspace_members as workspace_members_crud
from app.models.workspace import Workspace, WorkspaceRole
from app.models.workspace_invitation import WorkspaceInvitation, InvitationStatus

logger = logging.getLogger("app.services.workspace_invitation")


# ============================================================================
# Write Operations / Orchestration
# ============================================================================

def create_workspace_invitation(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    inviter_id: uuid.UUID,
    email: str,
    role: WorkspaceRole,
    expires_in_hours: int = 48,
) -> WorkspaceInvitation:
    """
    Orchestrates the creation of a workspace invitation.
    """
    normalized_email = email.strip().lower()

    inviter_membership = workspace_members_crud.get_membership(
        db, user_id=inviter_id, workspace_id=workspace_id
    )
    if not inviter_membership or not inviter_membership.is_active:
        raise InvitationPermissionDeniedError("Inviter is not an active member of this workspace.")

    if not workspace_permissions.can_invite_members(inviter_membership.role):
        raise InvitationPermissionDeniedError("Inviter does not possess permissions to invite members.")

    user = user_crud.get_user_by_email(db, email=normalized_email)
    if user:
        is_member = workspace_members_crud.membership_exists(
            db, user_id=user.id, workspace_id=workspace_id
        )
        if is_member:
            raise InvitationAlreadyMemberError("The recipient is already a member of this workspace.")

    try:
        existing_invite = workspace_invitation_crud.get_pending_invitation(
            db, workspace_id=workspace_id, email=normalized_email
        )
        if existing_invite:
            workspace_invitation_crud.mark_invitation_revoked(db, invitation=existing_invite)

        token = generate_secure_token()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)

        invitation = workspace_invitation_crud.create_invitation(
            db,
            workspace_id=workspace_id,
            inviter_id=inviter_id,
            email=normalized_email,
            role=role,
            token=token,
            expires_at=expires_at,
        )

        commit_and_refresh(db, invitation)

        logger.info(
            "AUDIT | INVITATION_CREATED | ID: %s | Workspace: %s | Email: %s | Role: %s | Inviter: %s",
            invitation.id, workspace_id, normalized_email, role, inviter_id
        )

        return invitation

    except Exception as e:
        rollback_and_log_error(
            db,
            logger,
            "Failed to create workspace invitation for %s in workspace %s: %s",
            normalized_email,
            workspace_id,
            str(e),
            exc=e,
        )


def revoke_workspace_invitation(
    db: Session,
    *,
    invitation_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> WorkspaceInvitation:
    """
    Orchestrates revoking an active pending invitation.
    """
    invitation = workspace_invitation_crud.get_invitation_by_id(db, invitation_id=invitation_id)
    if not invitation:
        raise InvitationNotFoundError("Invitation not found.")

    if invitation.status != InvitationStatus.PENDING:
        raise InvitationAlreadyProcessedError("Only pending invitations can be revoked.")

    actor_membership = workspace_members_crud.get_membership(
        db, user_id=actor_id, workspace_id=invitation.workspace_id
    )
    if not actor_membership or not actor_membership.is_active:
        raise InvitationPermissionDeniedError("Actor is not an active member of this workspace.")

    if not workspace_permissions.can_remove_members(actor_membership.role):
        raise InvitationPermissionDeniedError("Actor does not possess permissions to revoke invitations.")

    try:
        workspace_invitation_crud.mark_invitation_revoked(db, invitation=invitation)
        commit_and_refresh(db, invitation)

        logger.info(
            "AUDIT | INVITATION_REVOKED | ID: %s | Actor: %s",
            invitation_id, actor_id
        )

        return invitation

    except Exception as e:
        rollback_and_log_error(
            db,
            logger,
            "Failed to revoke workspace invitation ID %s: %s",
            invitation_id,
            str(e),
            exc=e,
        )


def resend_workspace_invitation(
    db: Session,
    *,
    invitation_id: uuid.UUID,
    actor_id: uuid.UUID,
    expires_in_hours: int = 48,
) -> WorkspaceInvitation:
    """
    Resends an invitation by ID.
    """
    invitation = workspace_invitation_crud.get_invitation_by_id(db, invitation_id=invitation_id)
    if not invitation:
        raise InvitationNotFoundError("Original invitation not found.")

    actor_membership = workspace_members_crud.get_membership(
        db, user_id=actor_id, workspace_id=invitation.workspace_id
    )
    if not actor_membership or not actor_membership.is_active:
        raise InvitationPermissionDeniedError("Actor is not an active member of this workspace.")

    if not workspace_permissions.can_invite_members(actor_membership.role):
        raise InvitationPermissionDeniedError("Actor does not possess permissions to invite members.")

    return create_workspace_invitation(
        db,
        workspace_id=invitation.workspace_id,
        inviter_id=actor_id,
        email=invitation.email,
        role=invitation.role,
        expires_in_hours=expires_in_hours,
    )


def accept_workspace_invitation(
    db: Session,
    *,
    token: str,
) -> WorkspaceInvitation:
    """
    Accepts an invitation using a secure token.
    """
    try:
        invitation = validate_invitation_token(db, token=token)

        user = user_crud.get_user_by_email(db, email=invitation.email)
        if not user or not user.is_active:
            raise InvitationPermissionDeniedError(
                "An active user account matching the invitation email is required to accept."
            )

        workspace_members_crud.create_membership(
            db,
            user_id=user.id,
            workspace_id=invitation.workspace_id,
            role=invitation.role,
            is_active=True,
        )

        workspace_invitation_crud.mark_invitation_accepted(db, invitation=invitation)
        commit_and_refresh(db, invitation)

        logger.info(
            "AUDIT | INVITATION_ACCEPTED | ID: %s | Workspace: %s | User: %s | Email: %s",
            invitation.id, invitation.workspace_id, user.id, invitation.email
        )

        return invitation

    except Exception as e:
        rollback_and_log_error(
            db,
            logger,
            "Failed to accept workspace invitation: %s",
            str(e),
            exc=e,
        )


def reject_workspace_invitation(
    db: Session,
    *,
    token: str,
) -> WorkspaceInvitation:
    """
    Validates the secure token and transitions the invitation status to REJECTED.
    """
    try:
        invitation = validate_invitation_token(db, token=token)
        workspace_invitation_crud.mark_invitation_rejected(db, invitation=invitation)
        commit_and_refresh(db, invitation)

        logger.info(
            "AUDIT | INVITATION_REJECTED | ID: %s | Workspace: %s | Email: %s",
            invitation.id, invitation.workspace_id, invitation.email
        )

        return invitation

    except Exception as e:
        rollback_and_log_error(
            db,
            logger,
            "Failed to reject workspace invitation: %s",
            str(e),
            exc=e,
        )


def preview_workspace_invitation(
    db: Session,
    *,
    token: str,
) -> dict:
    """
    Safely resolves and previews invitation parameters without mutating states.
    """
    invitation = workspace_invitation_crud.get_invitation_by_token(db, token=token)
    if not invitation:
        raise InvitationNotFoundError("The invitation link is invalid or has expired.")

    if invitation.status != InvitationStatus.PENDING:
        raise InvitationAlreadyProcessedError("This invitation has already been processed.")

    if invitation.expires_at <= datetime.now(timezone.utc):
        raise InvitationExpiredError("This invitation has expired.")

    workspace = db.get(Workspace, invitation.workspace_id)
    inviter_user = db.get(User, invitation.inviter_id)

    return {
        "workspace_name": workspace.workspace_name if workspace else "Workspace",
        "inviter_email": inviter_user.email if inviter_user else "Team Member",
        "invited_email": invitation.email,
        "role": invitation.role,
        "expires_at": invitation.expires_at,
    }


def expire_workspace_stale_invitations(
    db: Session,
    *,
    workspace_id: uuid.UUID,
) -> int:
    """
    Scans and transitions expired pending invitations inside a workspace to EXPIRED.
    """
    expired_count = 0
    try:
        pending_invitations = workspace_invitation_crud.list_pending_workspace_invitations(
            db, workspace_id=workspace_id
        )

        for invitation in pending_invitations:
            if workspace_invitation_crud.is_invitation_expired(invitation):
                workspace_invitation_crud.mark_invitation_expired(db, invitation=invitation)
                expired_count += 1

        if expired_count > 0:
            db.commit()
            logger.info("Successfully expired %d stale invitations in workspace: %s", expired_count, workspace_id)

        return expired_count

    except Exception as e:
        rollback_and_log_error(
            db,
            logger,
            "Failed to expire stale invitations in workspace %s: %s",
            workspace_id,
            str(e),
            exc=e,
        )


# ============================================================================
# Read-Only Operations
# ============================================================================

def get_workspace_invitations(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> list[WorkspaceInvitation]:
    """
    Retrieves all invitations associated with a given workspace.
    """
    actor_membership = workspace_members_crud.get_membership(
        db, user_id=actor_id, workspace_id=workspace_id
    )
    if not actor_membership or not actor_membership.is_active:
        raise InvitationPermissionDeniedError("Actor is not an active member of this workspace.")

    return workspace_invitation_crud.list_workspace_invitations(db, workspace_id=workspace_id)


def get_pending_workspace_invitations(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> list[WorkspaceInvitation]:
    """
    Retrieves all pending invitations associated with a given workspace.
    """
    actor_membership = workspace_members_crud.get_membership(
        db, user_id=actor_id, workspace_id=workspace_id
    )
    if not actor_membership or not actor_membership.is_active:
        raise InvitationPermissionDeniedError("Actor is not an active member of this workspace.")

    return workspace_invitation_crud.list_pending_workspace_invitations(db, workspace_id=workspace_id)


def validate_invitation_token(
    db: Session,
    *,
    token: str,
) -> WorkspaceInvitation:
    """
    Validates the active state and expiration of an invitation secure token.
    """
    invitation = workspace_invitation_crud.get_invitation_by_token(db, token=token)
    if not invitation or invitation.status != InvitationStatus.PENDING:
        raise InvalidInvitationTokenError("The invitation token is invalid or has already been processed.")

    # Check for expiration
    if workspace_invitation_crud.is_invitation_expired(invitation):
        try:
            workspace_invitation_crud.mark_invitation_expired(db, invitation=invitation)
            db.commit()
            db.refresh(invitation)
        except Exception as e:
            db.rollback()
            raise e
        raise InvitationExpiredError("The invitation has expired.")

    return invitation