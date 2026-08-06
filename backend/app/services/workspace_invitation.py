"""
Business orchestration for workspace invitations.

Rewritten for ARCH-01. The previous implementation referenced five symbols that
no longer exist after the tenancy transformation — get_membership,
membership_exists, create_membership, can_invite_members(WorkspaceRole), and
can_remove_members — so a patch was not available.

Two security defects identified in the architecture audit are closed here:

  B3 — Unauthenticated acceptance.
      accept and reject took only a token, and the service resolved the user by
      invitation.email. Any holder of the token could accept or reject on the
      invitee's behalf, and reject in particular gave a token holder a denial
      of service on the invitation. Both operations now require an
      authenticated actor whose email matches the invited address. The token
      identifies the invitation; the session identifies the actor.

  B5 — Invite-role escalation.
      create checked only "is the actor at least a Manager", never that the
      invited role sat below the inviter's. A Manager could invite their own
      alternate address as OWNER and self-escalate. can_assign_workspace_role
      is now applied at invitation time, because an invitation is a deferred
      role assignment and enforcing only on promotion leaves the path open.

Acceptance provisions BOTH an organization seat and a workspace grant in one
transaction, per invariant B.1 #2: organization membership is a precondition
for workspace access, not an alternative to it. CRUD is called directly rather
than through workspace_member_service.grant_workspace_access, which commits
internally and would split one logical operation across two transactions.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import (
    InvalidInvitationTokenError,
    InvitationAlreadyMemberError,
    InvitationAlreadyProcessedError,
    InvitationEmailMismatchError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationPermissionDeniedError,
)
from app.core.tokens import generate_secure_token
from app.core.transactions import commit_and_refresh, rollback_and_log_error
from app.core.workspace_permissions import can_assign_workspace_role
from app.crud import organization_members as organization_members_crud
from app.crud import user as user_crud
from app.crud import workspace as workspace_crud
from app.crud import workspace_invitation as workspace_invitation_crud
from app.crud import workspace_members as workspace_members_crud
from app.crud.membership_filters import ACTIVE_ONLY
from app.models.organization import (
    MembershipStatus,
    OrganizationRole,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceRole
from app.models.workspace_invitation import InvitationStatus, WorkspaceInvitation
from app.services.workspace_member_service import WorkspaceAccess

logger = logging.getLogger("app.services.workspace_invitation")

#: Default invitation lifetime. Long enough to survive a weekend, short enough
#: that a leaked link has a bounded window.
DEFAULT_EXPIRY_HOURS: int = 48


@dataclass(frozen=True)
class AcceptedInvitation:
    """
    The complete result of accepting an invitation.

    Carries the destination so the client can navigate straight into the
    workspace without a follow-up bootstrap call.
    """
    invitation: WorkspaceInvitation
    workspace: Workspace
    workspace_role: WorkspaceRole


# ============================================================================
# Token resolution
# ============================================================================

def validate_invitation_token(
    db: Session,
    *,
    token: str,
) -> WorkspaceInvitation:
    """
    Resolves a token to a live, pending invitation.

    Expiry is evaluated on access and persisted, so an expired invitation stops
    appearing as pending the moment anyone touches it. ARCH-04 adds a scheduled
    sweeper for invitations nobody ever opens.

    Raises:
        InvalidInvitationTokenError: No invitation matches the token.
        InvitationAlreadyProcessedError: Already accepted, rejected, or revoked.
        InvitationExpiredError: Past its expiry timestamp.
    """
    invitation = workspace_invitation_crud.get_invitation_by_token(
        db, token=token
    )
    if invitation is None:
        raise InvalidInvitationTokenError(
            "The invitation link is invalid or has expired."
        )

    if invitation.status is not InvitationStatus.PENDING:
        raise InvitationAlreadyProcessedError(
            "This invitation has already been processed."
        )

    if invitation.expires_at <= datetime.now(timezone.utc):
        try:
            workspace_invitation_crud.mark_invitation_expired(
                db, invitation=invitation
            )
            commit_and_refresh(db, invitation)
        except Exception as exc:
            rollback_and_log_error(
                db,
                logger,
                "Failed to mark invitation %s expired: %s",
                invitation.id,
                str(exc),
                exc=exc,
            )
        raise InvitationExpiredError("This invitation has expired.")

    return invitation


def _assert_actor_matches_invitation(
    *,
    invitation: WorkspaceInvitation,
    current_user: User,
) -> None:
    """
    Verifies the authenticated actor is the invited party.

    The core of the B3 fix. Without it, possession of the token is treated as
    proof of identity, and a token travels through forwarded email, shared
    inboxes, proxy logs, and referrer headers.
    """
    if (current_user.email or "").strip().lower() != (
        invitation.email or ""
    ).strip().lower():
        raise InvitationEmailMismatchError(
            f"This invitation was sent to {invitation.email}. You are signed "
            "in with a different account. Sign out and sign in with the "
            "invited address to continue."
        )


# ============================================================================
# Creation and management
# ============================================================================

def create_workspace_invitation(
    db: Session,
    *,
    workspace: Workspace,
    actor_access: WorkspaceAccess,
    email: str,
    role: WorkspaceRole,
    expires_in_hours: int = DEFAULT_EXPIRY_HOURS,
) -> WorkspaceInvitation:
    """
    Creates a pending invitation to a workspace.

    Authorization spans both tiers. can_assign_workspace_role requires the
    actor to hold workspace ADMIN, and additionally requires organization-level
    standing to invite at workspace ADMIN. This is the check whose absence
    allowed a Manager to invite at OWNER level before ARCH-01.

    A pending invitation to the same address is revoked and reissued rather
    than duplicated, so the newest link is always the only live one.
    """
    normalized_email = email.strip().lower()

    if not can_assign_workspace_role(
        actor_access.organization_role, actor_access.effective_role, role
    ):
        raise InvitationPermissionDeniedError(
            "You do not have permission to invite members at this role."
        )

    existing_user = user_crud.get_user_by_email(db, email=normalized_email)
    if existing_user is not None:
        grant = workspace_members_crud.get_workspace_member(
            db,
            workspace_id=workspace.id,
            user_id=existing_user.id,
            statuses=ACTIVE_ONLY,
        )
        if grant is not None:
            raise InvitationAlreadyMemberError(
                "The recipient is already a member of this workspace."
            )

    try:
        pending = workspace_invitation_crud.get_pending_invitation(
            db, workspace_id=workspace.id, email=normalized_email
        )
        if pending is not None:
            workspace_invitation_crud.mark_invitation_revoked(
                db, invitation=pending
            )

        invitation = workspace_invitation_crud.create_invitation(
            db,
            workspace_id=workspace.id,
            inviter_id=actor_access.actor_user_id,
            email=normalized_email,
            role=role,
            token=generate_secure_token(),
            expires_at=(
                datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
            ),
        )

        # Assigned after creation rather than passed in, so this rewrite does
        # not depend on the legacy CRUD signature. ARCH-04 moves invitations to
        # organization scope and makes the column non-nullable.
        invitation.organization_id = workspace.organization_id
        db.add(invitation)
        db.flush()

        commit_and_refresh(db, invitation)

        logger.info(
            "AUDIT | INVITATION_CREATED | ID: %s | Org: %s | Workspace: %s | "
            "Email: %s | Role: %s | Inviter: %s",
            invitation.id,
            workspace.organization_id,
            workspace.id,
            normalized_email,
            role.value,
            actor_access.actor_user_id,
        )
        return invitation

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to create invitation for %s in workspace %s: %s",
            normalized_email,
            workspace.id,
            str(exc),
            exc=exc,
        )


def list_pending_invitations(
    db: Session,
    *,
    workspace: Workspace,
) -> list[WorkspaceInvitation]:
    """
    Returns pending invitations for a workspace.

    Expiry is lazy, so a listed invitation may already be past its timestamp.
    The response carries expires_at and the client filters on it. ARCH-04's
    sweeper removes the discrepancy entirely.
    """
    return workspace_invitation_crud.list_pending_workspace_invitations(
        db, workspace_id=workspace.id
    )


def revoke_workspace_invitation(
    db: Session,
    *,
    workspace: Workspace,
    actor_access: WorkspaceAccess,
    invitation_id: uuid.UUID,
) -> WorkspaceInvitation:
    """
    Revokes a pending invitation.

    The invitation is re-fetched and its workspace verified against the
    addressed one. Without that check, an actor authorized for one workspace
    could revoke an invitation belonging to another by supplying its
    identifier.
    """
    invitation = _get_invitation_in_workspace(
        db, workspace=workspace, invitation_id=invitation_id
    )

    if invitation.status is not InvitationStatus.PENDING:
        raise InvitationAlreadyProcessedError(
            "Only pending invitations can be revoked."
        )

    if not can_assign_workspace_role(
        actor_access.organization_role,
        actor_access.effective_role,
        invitation.role,
    ):
        raise InvitationPermissionDeniedError(
            "You do not have permission to revoke this invitation."
        )

    try:
        workspace_invitation_crud.mark_invitation_revoked(
            db, invitation=invitation
        )
        commit_and_refresh(db, invitation)

        logger.info(
            "AUDIT | INVITATION_REVOKED | ID: %s | Workspace: %s | Actor: %s",
            invitation.id,
            workspace.id,
            actor_access.actor_user_id,
        )
        return invitation

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to revoke invitation %s: %s",
            invitation_id,
            str(exc),
            exc=exc,
        )


def resend_workspace_invitation(
    db: Session,
    *,
    workspace: Workspace,
    actor_access: WorkspaceAccess,
    invitation_id: uuid.UUID,
    expires_in_hours: int = DEFAULT_EXPIRY_HOURS,
) -> WorkspaceInvitation:
    """
    Reissues an invitation with a fresh token and expiry.

    Delegates to create_workspace_invitation, which revokes the original and
    re-runs every authorization check. A resend therefore cannot preserve a
    role the actor is no longer permitted to grant — the relevant case when the
    actor has been demoted since the original invitation.
    """
    invitation = _get_invitation_in_workspace(
        db, workspace=workspace, invitation_id=invitation_id
    )

    return create_workspace_invitation(
        db,
        workspace=workspace,
        actor_access=actor_access,
        email=invitation.email,
        role=invitation.role,
        expires_in_hours=expires_in_hours,
    )


def _get_invitation_in_workspace(
    db: Session,
    *,
    workspace: Workspace,
    invitation_id: uuid.UUID,
) -> WorkspaceInvitation:
    """
    Fetches an invitation and asserts it belongs to the addressed workspace.
    """
    invitation = workspace_invitation_crud.get_invitation_by_id(
        db, invitation_id=invitation_id
    )
    if invitation is None or invitation.workspace_id != workspace.id:
        raise InvitationNotFoundError("Invitation not found.")
    return invitation


# ============================================================================
# Recipient actions
# ============================================================================

def preview_workspace_invitation(
    db: Session,
    *,
    token: str,
) -> dict:
    """
    Resolves an invitation for public display, without mutating it.

    Served unauthenticated so a recipient can see what they are being asked to
    join before creating an account. Reads only; the write operations below are
    the ones that require an actor.
    """
    invitation = validate_invitation_token(db, token=token)

    workspace = workspace_crud.get_workspace_with_organization(
        db, workspace_id=invitation.workspace_id
    )
    if workspace is None:
        raise InvalidInvitationTokenError(
            "The invitation link is invalid or has expired."
        )

    inviter = user_crud.get_user_by_id(db, user_id=invitation.inviter_id)

    return {
        "organization_name": workspace.organization.name,
        "workspace_name": workspace.workspace_name,
        "inviter_email": inviter.email if inviter else "",
        "invited_email": invitation.email,
        "role": invitation.role,
        "expires_at": invitation.expires_at,
    }


def accept_workspace_invitation(
    db: Session,
    *,
    token: str,
    current_user: User,
) -> AcceptedInvitation:
    """
    Accepts an invitation on behalf of the authenticated actor.

    Provisions an organization seat and a workspace grant in one transaction.
    The seat is OrganizationRole.MEMBER even for a workspace ADMIN invitee:
    workspace administration and organization administration are separate
    authorities, and promotion to organization ADMIN is a deliberate, separate
    act.

    Existing rows are reactivated rather than duplicated, so a re-invited
    former member keeps their history on one record. The unique constraints
    would reject a duplicate in any case.

    Raises:
        InvitationEmailMismatchError: The actor is not the invited party.
        InvitationAlreadyMemberError: Already an active member.
    """
    invitation = validate_invitation_token(db, token=token)
    _assert_actor_matches_invitation(
        invitation=invitation, current_user=current_user
    )

    workspace = workspace_crud.get_workspace_with_organization(
        db, workspace_id=invitation.workspace_id
    )
    if workspace is None:
        raise InvalidInvitationTokenError(
            "The workspace for this invitation no longer exists."
        )

    existing_grant = workspace_members_crud.get_workspace_member(
        db,
        workspace_id=workspace.id,
        user_id=current_user.id,
        statuses=ACTIVE_ONLY,
    )
    if existing_grant is not None:
        raise InvitationAlreadyMemberError(
            "You are already a member of this workspace."
        )

    try:
        # 1. Organization seat. Invariant B.1 #2 — the seat is what authorizes
        #    the actor's presence in the tenant at all.
        seat = organization_members_crud.get_organization_member(
            db,
            organization_id=workspace.organization_id,
            user_id=current_user.id,
            statuses=None,
        )
        if seat is None:
            organization_members_crud.create_organization_member(
                db,
                organization_id=workspace.organization_id,
                user_id=current_user.id,
                role=OrganizationRole.MEMBER,
                status=MembershipStatus.ACTIVE,
            )
        elif seat.status is not MembershipStatus.ACTIVE:
            organization_members_crud.reactivate_organization_member(
                db, membership=seat
            )

        # 2. Workspace grant.
        grant = workspace_members_crud.get_workspace_member(
            db,
            workspace_id=workspace.id,
            user_id=current_user.id,
            statuses=None,
        )
        if grant is None:
            workspace_members_crud.create_workspace_member(
                db,
                workspace_id=workspace.id,
                user_id=current_user.id,
                role=invitation.role,
                status=MembershipStatus.ACTIVE,
            )
        else:
            workspace_members_crud.reactivate_workspace_member(
                db, membership=grant, role=invitation.role
            )

        # 3. Consume the invitation.
        workspace_invitation_crud.mark_invitation_accepted(
            db, invitation=invitation
        )

        commit_and_refresh(db, invitation)
        db.refresh(workspace)

        logger.info(
            "AUDIT | INVITATION_ACCEPTED | ID: %s | Org: %s | Workspace: %s | "
            "User: %s | Role: %s",
            invitation.id,
            workspace.organization_id,
            workspace.id,
            current_user.id,
            invitation.role.value,
        )

        return AcceptedInvitation(
            invitation=invitation,
            workspace=workspace,
            workspace_role=invitation.role,
        )

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to accept invitation %s for user %s: %s",
            invitation.id,
            current_user.id,
            str(exc),
            exc=exc,
        )


def reject_workspace_invitation(
    db: Session,
    *,
    token: str,
    current_user: User,
) -> WorkspaceInvitation:
    """
    Declines an invitation on behalf of the authenticated actor.

    Authenticated for the same reason as acceptance. Unauthenticated rejection
    let any token holder burn an invitation the recipient had not seen — a
    denial of service requiring nothing but the link.
    """
    invitation = validate_invitation_token(db, token=token)
    _assert_actor_matches_invitation(
        invitation=invitation, current_user=current_user
    )

    try:
        workspace_invitation_crud.mark_invitation_rejected(
            db, invitation=invitation
        )
        commit_and_refresh(db, invitation)

        logger.info(
            "AUDIT | INVITATION_REJECTED | ID: %s | Workspace: %s | User: %s",
            invitation.id,
            invitation.workspace_id,
            current_user.id,
        )
        return invitation

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to reject invitation %s: %s",
            invitation.id,
            str(exc),
            exc=exc,
        )