from __future__ import annotations

import asyncio
import logging
import uuid
from concurrent.futures import Future
from datetime import datetime, timezone, timedelta
from typing import Any

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
# Private Synchronous-to-Asyncio Bridge
# ============================================================================

def _run_async(coro: Any) -> Any:
    """
    Safely executes an async coroutine from a synchronous thread.
    
    Reuses the running event loop when called from FastAPI's threadpool executor, 
    or launches a new event loop as appropriate.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    if loop.is_running():
        future = Future()

        def callback():
            async def wrapper():
                try:
                    result = await coro
                    future.set_result(result)
                except Exception as exc:
                    future.set_exception(exc)
            asyncio.create_task(wrapper())

        loop.call_soon_threadsafe(callback)
        return future.result()
    else:
        return asyncio.run(coro)


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
    
    Performs email normalization, business validations (inviter active status, 
    invite authority, recipient current membership, duplicate pending invitations), 
    secure token generation, and transaction execution.
    """
    normalized_email = email.strip().lower()

    # 1. Validate inviter identity and authorization permissions
    inviter_membership = workspace_members_crud.get_membership(
        db, user_id=inviter_id, workspace_id=workspace_id
    )
    if not inviter_membership or not inviter_membership.is_active:
        raise InvitationPermissionDeniedError("Inviter is not an active member of this workspace.")

    if not workspace_permissions.can_invite_members(inviter_membership.role):
        raise InvitationPermissionDeniedError("Inviter does not possess permissions to invite members.")

    # 2. Prevent inviting a user who is already an active member of the workspace
    user = user_crud.get_user_by_email(db, email=normalized_email)
    if user:
        is_member = workspace_members_crud.membership_exists(
            db, user_id=user.id, workspace_id=workspace_id
        )
        if is_member:
            raise InvitationAlreadyMemberError("The recipient is already a member of this workspace.")

    try:
        # 3. Handle duplicates: revoke any existing active pending invitation for this recipient
        existing_invite = workspace_invitation_crud.get_pending_invitation(
            db, workspace_id=workspace_id, email=normalized_email
        )
        if existing_invite:
            workspace_invitation_crud.mark_invitation_revoked(db, invitation=existing_invite)

        # 4. Generate cryptographically secure invitation token and calculate expiration
        token = generate_secure_token()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)

        # 5. Persist invitation record
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

        # 6. Trigger outbound email delivery using the central Notification Service
        try:
            workspace = db.get(Workspace, workspace_id)
            workspace_name = workspace.workspace_name if workspace else "Workspace"

            from app.services.notification_service import notification_service
            success = _run_async(
                notification_service.send_workspace_invitation(
                    db=db,
                    invitation=invitation,
                    workspace_name=workspace_name,
                )
            )

            if not success:
                logger.error(
                    "SMTP_DELIVERY_FAILURE | Recipient: %s | WorkspaceID: %s | InvitationID: %s",
                    normalized_email,
                    workspace_id,
                    invitation.id,
                )
            else:
                logger.info(
                    "SMTP_DELIVERY_SUCCESS | Recipient: %s | WorkspaceID: %s | InvitationID: %s",
                    normalized_email,
                    workspace_id,
                    invitation.id,
                )
        except Exception as email_err:
            # Catch defensively: failures in SMTP routing are completely non-blocking
            logger.error(
                "SMTP_DELIVERY_CRITICAL_EXCEPTION | Recipient: %s | WorkspaceID: %s | InvitationID: %s | Error: %s",
                normalized_email,
                workspace_id,
                invitation.id,
                str(email_err),
            )

        logger.info(
            "Successfully created workspace invitation. ID: %s, Workspace: %s, Recipient: %s",
            invitation.id,
            workspace_id,
            normalized_email,
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
    
    Verifies that the actor possesses appropriate member management permissions.
    """
    # 1. Fetch invitation record
    invitation = workspace_invitation_crud.get_invitation_by_id(db, invitation_id=invitation_id)
    if not invitation:
        raise InvitationNotFoundError("Invitation not found.")

    if invitation.status != InvitationStatus.PENDING:
        raise InvitationAlreadyProcessedError("Only pending invitations can be revoked.")

    # 2. Validate actor permissions
    actor_membership = workspace_members_crud.get_membership(
        db, user_id=actor_id, workspace_id=invitation.workspace_id
    )
    if not actor_membership or not actor_membership.is_active:
        raise InvitationPermissionDeniedError("Actor is not an active member of this workspace.")

    if not workspace_permissions.can_remove_members(actor_membership.role):
        raise InvitationPermissionDeniedError("Actor does not possess permissions to revoke invitations.")

    try:
        # 3. Transition invitation status to REVOKED
        workspace_invitation_crud.mark_invitation_revoked(db, invitation=invitation)
        commit_and_refresh(db, invitation)

        logger.info("Successfully revoked workspace invitation ID: %s", invitation_id)
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
    
    Handles duplicates idempotently by revoking the old record (even if pending) 
    and generating a fresh pending invitation with a new secure token.
    """
    # 1. Fetch original invitation
    invitation = workspace_invitation_crud.get_invitation_by_id(db, invitation_id=invitation_id)
    if not invitation:
        raise InvitationNotFoundError("Original invitation not found.")

    # 2. Validate actor membership and permissions
    actor_membership = workspace_members_crud.get_membership(
        db, user_id=actor_id, workspace_id=invitation.workspace_id
    )
    if not actor_membership or not actor_membership.is_active:
        raise InvitationPermissionDeniedError("Actor is not an active member of this workspace.")

    if not workspace_permissions.can_invite_members(actor_membership.role):
        raise InvitationPermissionDeniedError("Actor does not possess permissions to invite members.")

    # 3. Re-delegate to create_workspace_invitation which safely revokes the old one and handles transaction
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
    
    Verifies that the invitation is valid and not expired, maps the recipient 
    email to an existing active User account, registers the user in the workspace_members 
    table, and transitions the invitation status to ACCEPTED.
    """
    try:
        # 1. Validate the secure token and check for expiration
        invitation = validate_invitation_token(db, token=token)

        # 2. Retrieve corresponding user account
        user = user_crud.get_user_by_email(db, email=invitation.email)
        if not user or not user.is_active:
            raise InvitationPermissionDeniedError(
                "An active user account matching the invitation email is required to accept."
            )

        # 3. Register user as a member of the workspace (or idempotently ignore if already active)
        workspace_members_crud.create_membership(
            db,
            user_id=user.id,
            workspace_id=invitation.workspace_id,
            role=invitation.role,
            is_active=True,
        )

        # 4. Transition invitation status to ACCEPTED
        workspace_invitation_crud.mark_invitation_accepted(db, invitation=invitation)

        commit_and_refresh(db, invitation)

        logger.info(
            "Successfully accepted workspace invitation ID: %s. User %s joined workspace %s",
            invitation.id,
            user.id,
            invitation.workspace_id,
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
        # 1. Validate token state and expiration
        invitation = validate_invitation_token(db, token=token)

        # 2. Transition status to REJECTED
        workspace_invitation_crud.mark_invitation_rejected(db, invitation=invitation)
        commit_and_refresh(db, invitation)

        logger.info("Successfully rejected workspace invitation ID: %s", invitation.id)
        return invitation

    except Exception as e:
        rollback_and_log_error(
            db,
            logger,
            "Failed to reject workspace invitation: %s",
            str(e),
            exc=e,
        )


def expire_workspace_stale_invitations(
    db: Session,
    *,
    workspace_id: uuid.UUID,
) -> int:
    """
    Scans and transitions expired pending invitations inside a workspace to EXPIRED.
    
    Returns the count of updated invitations.
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
    
    Enforces active membership check on the requesting actor.
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
    
    Enforces active membership check on the requesting actor.
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
    
    If the token has expired, transition status to EXPIRED and commit transaction.
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