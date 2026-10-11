"""
Business orchestration for the ARCH-04 invitation lifecycle.

Owns the transaction boundary for issuance, acceptance, rejection,
revocation, and resend. Authorization delegates to
app.core.organization_permissions; persistence to app.crud.

ARCH-07 Step 3: Converted AUDIT log sites to audit_service.record().
All data carriers, seat management, grant resolutions, token resolutions, and sweeper
functions are 100% preserved.
"""

from __future__ import annotations

import logging
import uuid
import sqlalchemy as sa
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    InvalidInvitationTokenError,
    InvitationAccountExistsError,
    LastOwnerError,
    InvitationAlreadyExistsError,
    InvitationAlreadyMemberError,
    InvitationAlreadyProcessedError,
    InvitationEmailMismatchError,
    InvitationExpiredError,
    InvitationGrantError,
    InvitationNotFoundError,
    InvitationPermissionDeniedError,
    InvitationResendTooSoonError,
    InvitationSignupUnavailableError,
    InvitationSsoRequiredError,
    SeatLimitExceededError,
)
from app.core.links import build_invitation_accept_link
from app.core.organization_permissions import (
    can_assign_organization_role,
    can_invite_members,
)
from app.core.tokens import generate_secure_token, hash_token
from app.core.transactions import commit_and_refresh, identity_of, rollback_and_log_error
from app.crud import organization_invitation as invitation_crud
from app.crud import organization_members as organization_members_crud
from app.crud import user as user_crud
from app.crud import workspace as workspace_crud
from app.crud import workspace_members as workspace_members_crud
from app.services import audit_service, seat_capacity_service, verification_service
from app.services.billing import seat_service as billing_seat_service
from app.services.organization_member_service import (
    lock_organization_for_owner_change,
)
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationRole,
)
from app.models.organization_invitation import (
    InvitationStatus,
    OrganizationInvitation,
)
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.user import User
from app.models.workspace import WorkspaceRole, WorkspaceStatus
from app.templates.emails.common import ExpiredInvitationLine, GrantLine, format_timestamp

logger = logging.getLogger("app.services.organization_invitation")


# ===========================================================================
# Carriers
# ===========================================================================

@dataclass(frozen=True)
class IssuedInvitation:
    """
    A new invitation plus the plaintext token that addresses it.
    """
    invitation: OrganizationInvitation
    plaintext_token: str
    organization_name: str
    inviter_email: str
    inviter_display: str
    grant_lines: list[GrantLine]

    @property
    def accept_link(self) -> str:
        return build_invitation_accept_link(self.plaintext_token)


def _display_name(user: User | None, fallback: str = "") -> str:
    if user is None:
        return fallback
    return user.display_name or user.email


@dataclass(frozen=True)
class AcceptedInvitation:
    """What acceptance produced, and what the inviter needs to be told."""
    invitation_id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    organization_slug: str
    inviter_email: str
    inviter_display: str
    invited_email: str
    invited_display: str
    organization_role: OrganizationRole
    provisioned_grants: list[GrantLine] = field(default_factory=list)
    skipped_grant_count: int = 0
    # F-107. Where the client should take the new member: the first workspace granted.
    first_workspace_slug: str | None = None


@dataclass(frozen=True)
class ResolvedInvitationParties:
    """Addresses and names for a notice, resolved before the carrier is built."""
    organization_name: str
    organization_slug: str
    inviter_email: str
    inviter_display: str
    invited_email: str
    invited_display: str
    invitation_id: uuid.UUID


@dataclass
class ExpiryDigestBatch:
    """
    One inviter's lapsed invitations, ready to render.
    """
    inviter_email: str
    organization_slug: str
    lines: list[ExpiredInvitationLine]


# ===========================================================================
# Seats — §0
# ===========================================================================

def count_reserved_seats(db: Session, *, organization_id: uuid.UUID) -> int:
    return (
        organization_members_crud.count_consumed_seats(
            db, organization_id=organization_id
        )
        + invitation_crud.count_pending_invitations(
            db, organization_id=organization_id
        )
    )


def _assert_seat_available(
    db: Session,
    *,
    organization: Organization,
    message: str | None = None,
    already_reserved: bool = False,
) -> None:
    """Refuse when the operation would need a seat the organization does not have.

    Campaign session 1: the one seat check every entry path shares
    (`seat_capacity_service`), which reads the plan's seats on Free and the purchased
    seats on a paid plan. Before, the only ceiling was `organizations.seat_limit`,
    which nothing writes, so every organization had unlimited seats.

    F-221. `already_reserved` is for an operation on a pending invitation, whose
    seat the count already includes: accepting turns that reserved seat into a
    member's, and resending reserves nothing new. They are refused only when the
    count is already past the capacity (a member added another way meanwhile, or
    fewer seats after a plan change).
    """
    seat_capacity_service.assert_seat_available(
        db,
        organization_id=organization.id,
        message=message,
        already_reserved=already_reserved,
    )


# ===========================================================================
# Grant resolution — §D6.4
# ===========================================================================

def _resolve_grants(
    db: Session,
    *,
    organization: Organization,
    requested: list[tuple[uuid.UUID, WorkspaceRole]],
) -> list[GrantLine]:
    if len(requested) > settings.INVITATION_MAX_GRANTS:
        raise InvitationGrantError(
            f"An invitation may carry at most "
            f"{settings.INVITATION_MAX_GRANTS} workspace grants."
        )

    seen: set[uuid.UUID] = set()
    lines: list[GrantLine] = []

    for workspace_id, role in requested:
        if workspace_id in seen:
            raise InvitationGrantError(
                "The same workspace appears more than once in this invitation."
            )
        seen.add(workspace_id)

        workspace = workspace_crud.get_workspace_by_id(
            db, workspace_id=workspace_id
        )

        if workspace is None or workspace.organization_id != organization.id:
            raise InvitationGrantError(
                "One or more workspaces in this invitation could not be found "
                "in this organization."
            )

        if workspace.status is not WorkspaceStatus.ACTIVE:
            raise InvitationGrantError(
                f"'{workspace.workspace_name}' is not active and cannot be "
                f"granted."
            )

        lines.append(GrantLine(
            workspace_name=workspace.workspace_name,
            role_display=role.value,
        ))

    return lines


# ===========================================================================
# Issuance
# ===========================================================================

def create_invitation(
    db: Session,
    *,
    organization: Organization,
    inviter: User,
    actor_role: OrganizationRole,
    email: str,
    organization_role: OrganizationRole,
    grants: list[tuple[uuid.UUID, WorkspaceRole]] | None = None,
    request: Any = None,
) -> IssuedInvitation:
    context = audit_service.context_from_request(request)
    grants = grants or []
    normalized = email.strip().lower()

    if not can_invite_members(actor_role):
        raise InvitationPermissionDeniedError(
            "You do not have permission to invite members to this organization."
        )

    if organization_role is OrganizationRole.OWNER:
        raise InvitationPermissionDeniedError(
            "Ownership cannot be granted by invitation. Invite the person as "
            "an administrator, then transfer ownership once they have joined."
        )

    if not can_assign_organization_role(actor_role, organization_role):
        raise InvitationPermissionDeniedError(
            "You do not have permission to invite someone at this role."
        )

    if normalized == inviter.email.strip().lower():
        raise InvitationAlreadyMemberError(
            "You are already a member of this organization."
        )

    existing_user = user_crud.get_user_by_email(db, email=normalized)
    if existing_user is not None:
        membership = organization_members_crud.get_organization_member(
            db, organization_id=organization.id, user_id=existing_user.id
        )
        if (
            membership is not None
            and membership.status is not MembershipStatus.DEACTIVATED
        ):
            raise InvitationAlreadyMemberError(
                "That person is already a member of this organization."
            )

    # Campaign session 1. The seat lock comes first, before any invitation row is
    # touched: superseding a pending invitation locks its row, and taking the seat
    # lock only afterwards deadlocked against a concurrent invitation to the same
    # address that held the seat lock and waited on that row through the
    # one-pending-invitation-per-address index.
    seat_capacity_service.lock_seats(db, organization_id=organization.id)

    existing_pending = invitation_crud.get_pending_invitation_for_email(
        db, organization_id=organization.id, email=normalized
    )
    if existing_pending is not None:
        # F-209. This called `update_invitation_status`, which the CRUD module
        # never had: a second invitation to the same address was a 500.
        invitation_crud.revoke_invitation(
            db,
            invitation_id=existing_pending.id,
            revoked_by_id=inviter.id,
            now=datetime.now(UTC),
        )
        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=inviter.id,
            resource_type=AuditResourceType.INVITATION,
            resource_id=existing_pending.id,
            action=AuditAction.REVOKED,
            details={
                **audit_service.actor_snapshot(inviter),
                "reason": "SUPERSEDED_BY_NEW_INVITATION",
                "recipient_email": normalized,
            },
            **context,
        )

    grant_lines = _resolve_grants(
        db, organization=organization, requested=grants
    )

    # The refusal explains itself: the plan's seats on Free, the purchased seats
    # (and how to add one) on a paid plan.
    _assert_seat_available(db, organization=organization)

    plaintext = generate_secure_token()
    now = datetime.now(UTC)

    try:
        invitation = invitation_crud.create_invitation(
            db,
            organization_id=organization.id,
            inviter_id=inviter.id,
            invited_user_id=existing_user.id if existing_user else None,
            email=normalized,
            organization_role=organization_role,
            token_hash=hash_token(plaintext),
            expires_at=now + timedelta(hours=settings.INVITATION_TTL_HOURS),
        )
        invitation_crud.add_workspace_grants(
            db, invitation_id=invitation.id, grants=grants
        )

        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=inviter.id,
            resource_type=AuditResourceType.INVITATION,
            resource_id=invitation.id,
            action=AuditAction.CREATED,
            details={
                **audit_service.actor_snapshot(inviter),
                "recipient_email": normalized,
                "organization_role": organization_role.value,
                "workspace_grants_count": len(grants),
                "expires_at": invitation.expires_at.isoformat(),
            },
            **context,
        )

        commit_and_refresh(db, invitation)

        return IssuedInvitation(
            invitation=invitation,
            plaintext_token=plaintext,
            organization_name=organization.name,
            inviter_email=inviter.email,
            inviter_display=_display_name(inviter, inviter.email),
            grant_lines=grant_lines,
        )

    except Exception as exc:
        rollback_and_log_error(
            db, logger,
            "Failed to issue invitation for organization %s: %s",
            identity_of(organization), str(exc), exc=exc,
        )


# ===========================================================================
# Token resolution
# ===========================================================================

def _load_by_token(db: Session, *, token: str) -> OrganizationInvitation:
    invitation = invitation_crud.get_invitation_by_token_hash(
        db, token_hash=hash_token(token)
    )
    if invitation is None:
        raise InvalidInvitationTokenError("This invitation link is invalid.")
    return invitation


def _classify_claim_failure(invitation: OrganizationInvitation) -> Exception:
    if invitation.status is not InvitationStatus.PENDING:
        return InvitationAlreadyProcessedError(
            f"This invitation was already "
            f"{invitation.status.value.lower()}."
        )
    return InvitationExpiredError(
        "This invitation has expired. Ask for a new one."
    )


def _assert_actor_matches(
    *, invitation: OrganizationInvitation, actor: User
) -> None:
    if actor.email.strip().lower() != invitation.email.strip().lower():
        raise InvitationEmailMismatchError(
            f"This invitation was sent to {invitation.email}. Sign in with "
            f"that address to accept it."
        )


def preview_invitation(db: Session, *, token: str) -> dict:
    invitation = _load_by_token(db, token=token)

    if invitation.status is not InvitationStatus.PENDING:
        raise InvitationAlreadyProcessedError(
            f"This invitation was already {invitation.status.value.lower()}."
        )
    if invitation.expires_at <= datetime.now(UTC):
        raise InvitationExpiredError("This invitation has expired.")

    organization = invitation.organization
    inviter = user_crud.get_user_by_id(db, user_id=invitation.inviter_id)

    return {
        "organization_name": organization.name,
        "inviter_email": inviter.email if inviter else "",
        "inviter_display": _display_name(inviter),
        "invited_email": invitation.email,
        "organization_role": invitation.organization_role,
        "workspaces": [
            {"name": g.workspace.workspace_name, "role": g.role}
            for g in invitation.grants
            if g.workspace is not None
        ],
        "expires_at": invitation.expires_at,
        # F-226. Unknown (None) when the token left through the organization's own
        # mail server: its operator holds the token and must not learn from it
        # whether an address has an account.
        "has_account": (
            None
            if invitation.delivered_off_platform
            else _has_verified_account(db, email=invitation.email)
        ),
        "sso_required": _sso_required(db, invitation=invitation),
    }


def _has_verified_account(db: Session, *, email: str) -> bool:
    """N-034. An account nobody ever verified is not one to sign in to: the
    invitation sign-up takes it over."""
    user = user_crud.get_user_by_email(db, email=email)
    return user is not None and user.email_verified_at is not None


def _sso_required(db: Session, *, invitation: OrganizationInvitation) -> bool:
    """N-035. Read-only: whether the organization requires SSO for the invited role."""
    from app.models.identity import TenantSecurityPolicy
    from app.services.identity import session_policy_service

    policy = db.execute(
        select(TenantSecurityPolicy).where(
            TenantSecurityPolicy.organization_id == invitation.organization_id
        )
    ).scalar_one_or_none()
    if policy is None:
        return False
    return session_policy_service.sso_required_for(
        policy, org_role=invitation.organization_role.value
    )


#: N-036. Accepting never lowers a role. BILLING and MEMBER differ in kind, not
#: rank: neither replaces the other on acceptance.
_ROLE_RANK = {
    OrganizationRole.OWNER: 3,
    OrganizationRole.ADMIN: 2,
    OrganizationRole.MEMBER: 1,
    OrganizationRole.BILLING: 1,
}


def describe_seat_blocked(db: Session, *, token: str) -> dict:
    invitation = _load_by_token(db, token=token)
    organization = invitation.organization
    inviter = user_crud.get_user_by_id(db, user_id=invitation.inviter_id)
    return {
        "invitation_id": invitation.id,
        "invited_email": invitation.email,
        "organization_name": organization.name,
        "organization_slug": organization.slug,
        "seat_limit": organization.seat_limit,
        "inviter_email": inviter.email if inviter else "",
    }


# ===========================================================================
# Acceptance
# ===========================================================================

def accept_invitation(
    db: Session,
    *,
    token: str,
    actor: User,
    request: Any = None,
) -> AcceptedInvitation:
    context = audit_service.context_from_request(request)
    invitation = _load_by_token(db, token=token)
    _assert_actor_matches(invitation=invitation, actor=actor)

    organization = invitation.organization
    inviter = user_crud.get_user_by_id(db, user_id=invitation.inviter_id)
    now = datetime.now(UTC)

    lock_organization_for_owner_change(db, organization_id=organization.id)

    owners_before = organization_members_crud.count_active_owners(
        db, organization_id=organization.id
    )

    _assert_seat_available(
        db,
        organization=organization,
        message=(
            f"{organization.name} has no seats available, so you could not be "
            f"added. Your invitation is still valid — ask an administrator to "
            f"free a seat, then open your link again."
        ),
        already_reserved=True,
    )

    try:
        claimed_id = invitation_crud.claim_invitation(
            db,
            token_hash=hash_token(token),
            new_status=InvitationStatus.ACCEPTED,
            now=now,
            actor_id=actor.id,
        )
        if claimed_id is None:
            raise _classify_claim_failure(invitation)

        membership = organization_members_crud.get_organization_member(
            db, organization_id=organization.id, user_id=actor.id
        )

        applied_role = invitation.organization_role
        role_preserved = False

        if membership is None:
            membership = organization_members_crud.create_organization_member(
                db,
                organization_id=organization.id,
                user_id=actor.id,
                role=applied_role,
                status=MembershipStatus.ACTIVE,
            )
        else:
            db.refresh(membership)

            # N-036 (owner decision 2026-10-10): accepting raises an active
            # member's role, never lowers it (an owner stays owner, an admin
            # promoted after the invitation was sent stays admin). A
            # deactivated member rejoins with the invitation's role.
            if (
                membership.status is MembershipStatus.ACTIVE
                and _ROLE_RANK[membership.role] >= _ROLE_RANK[applied_role]
            ):
                applied_role = membership.role
                role_preserved = True
            else:
                organization_members_crud.update_organization_member_role(
                    db, membership=membership, role=applied_role
                )

            organization_members_crud.set_organization_member_status(
                db, membership=membership, status=MembershipStatus.ACTIVE,
            )

        # ARCH-15 Step 15.4 (F4). *This* is when an invitation becomes a
        # seat — not when it was sent. A pending invitation is not billable
        # and never was; acceptance is the transition, and the transition is
        # where the event belongs.
        billing_seat_service.record_seat_added(
            db,
            organization_id=organization.id,
            membership_id=(membership.id if membership is not None else None),
            user_id=actor.id,
            cause="invitation_accepted",
        )

        provisioned: list[GrantLine] = []
        first_workspace_slug: str | None = None
        skipped = 0

        for grant in invitation.grants:
            workspace = grant.workspace
            if workspace is None or workspace.status is not WorkspaceStatus.ACTIVE:
                skipped += 1
                continue

            existing = workspace_members_crud.get_workspace_member(
                db, workspace_id=workspace.id, user_id=actor.id
            )
            if existing is None:
                workspace_members_crud.create_workspace_member(
                    db,
                    workspace_id=workspace.id,
                    user_id=actor.id,
                    role=grant.role,
                    status=MembershipStatus.ACTIVE,
                )
            else:
                workspace_members_crud.update_workspace_member_role(
                    db, membership=existing, role=grant.role
                )
                workspace_members_crud.set_workspace_member_status(
                    db, membership=existing, status=MembershipStatus.ACTIVE
                )

            provisioned.append(GrantLine(
                workspace_name=workspace.workspace_name,
                role_display=grant.role.value,
            ))
            if first_workspace_slug is None:
                first_workspace_slug = workspace.slug

        if owners_before >= 1:
            owners_after = organization_members_crud.count_active_owners(
                db, organization_id=organization.id
            )
            if owners_after < 1:
                raise LastOwnerError(
                    f"Accepting this invitation would leave {organization.name} "
                    f"without an active owner."
                )

        # F-226. Accepting proves the address only when FlowPilot's own relay
        # delivered the token; through the organization's mail server, whoever
        # runs that server holds it too.
        if actor.email_verified_at is None and not invitation.delivered_off_platform:
            actor.email_verified_at = now
            db.add(actor)
            
            from app.models.auth_token import AuthToken, AuthTokenPurpose
            db.execute(
                sa.delete(AuthToken).where(
                    AuthToken.user_id == actor.id,
                    AuthToken.purpose == AuthTokenPurpose.EMAIL_VERIFICATION
                )
            )

        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=actor.id,
            resource_type=AuditResourceType.INVITATION,
            resource_id=invitation.id,
            action=AuditAction.ACCEPTED,
            details={
                **audit_service.actor_snapshot(actor),
                "organization_role": applied_role.value,
                "role_preserved": role_preserved,
                "provisioned_grants_count": len(provisioned),
            },
            **context,
        )

        commit_and_refresh(db, invitation)

        return AcceptedInvitation(
            invitation_id=invitation.id,
            organization_id=organization.id,
            organization_name=organization.name,
            organization_slug=organization.slug,
            inviter_email=inviter.email if inviter else "",
            inviter_display=_display_name(inviter),
            invited_display=_display_name(actor, invitation.email),
            invited_email=invitation.email,
            organization_role=applied_role,
            provisioned_grants=provisioned,
            skipped_grant_count=skipped,
            first_workspace_slug=first_workspace_slug,
        )

    except Exception as exc:
        rollback_and_log_error(
            db, logger,
            "Failed to accept invitation %s: %s",
            identity_of(invitation), str(exc), exc=exc,
        )


def register_and_accept(
    db: Session,
    *,
    token: str,
    password: str,
    request: Any = None,
) -> tuple[User, AcceptedInvitation]:
    """F-222. Create the invited person's account and accept the invitation, in one transaction.

    The invitation decides the address; the token, which FlowPilot's relay
    emailed to it, proves the caller controls it, so the account starts
    verified (F-226: a token sent through the organization's own mail server
    proves nothing and is refused here). Nothing is
    created unless the acceptance succeeds: an expired or used invitation, a
    weak password, an address that already has an account (its owner signs in
    and accepts instead) or a full organization leaves no user behind.
    """
    from app.core import password_policy, security

    invitation = _load_by_token(db, token=token)
    if invitation.status is not InvitationStatus.PENDING:
        raise InvitationAlreadyProcessedError(
            f"This invitation was already {invitation.status.value.lower()}."
        )
    if invitation.expires_at <= datetime.now(UTC):
        raise InvitationExpiredError("This invitation has expired. Ask for a new one.")
    # F-226. Checked before anything about the address, so the answer says nothing about it.
    if invitation.delivered_off_platform:
        raise InvitationSignupUnavailableError(
            "This invitation was sent through your organization's own mail server, so it cannot "
            "confirm your address by itself. Create your account on the sign-up page, verify the "
            "email we send you, then open this invitation again."
        )

    # N-035. Joined through single sign-on, never with a password account.
    if _sso_required(db, invitation=invitation):
        raise InvitationSsoRequiredError(
            f"{invitation.organization.name} requires single sign-on. Sign in with your "
            f"company account to accept this invitation."
        )

    email = invitation.email.strip().lower()
    password_policy.check_new_password(password, email=email)
    existing = user_crud.get_user_by_email(db, email=email)
    if existing is not None and existing.email_verified_at is not None:
        raise InvitationAccountExistsError(
            f"An account already exists for {email}. Sign in to accept the invitation."
        )

    if existing is not None:
        # N-034 (owner decision 2026-10-10). Nobody ever proved this address, and
        # the token FlowPilot's relay delivered to it does: the invitee takes the
        # account over. A new password, the address verified, and every session
        # of whoever registered it ended.
        from app.models.user_session import SessionRevokedReason
        from app.services import session_service

        existing.hashed_password = security.get_password_hash(password)
        existing.email_verified_at = datetime.now(UTC)
        existing.is_active = True
        session_service.revoke_all_user_sessions(
            db, user=existing, reason=SessionRevokedReason.PASSWORD_CHANGE
        )
        user = existing
        logger.warning(
            "INVITATION_SIGNUP_TOOK_OVER_UNVERIFIED | user=%s | invitation=%s",
            existing.id, invitation.id,
        )
    else:
        user = User(
            email=email,
            hashed_password=security.get_password_hash(password),
            is_active=True,
            email_verified_at=datetime.now(UTC),
        )
    db.add(user)
    try:
        try:
            db.flush()
        except sa.exc.IntegrityError as exc:
            # The same link submitted twice at once: the other request created the
            # account first (users.email is unique).
            raise InvitationAccountExistsError(
                f"An account already exists for {email}. Sign in to accept the invitation."
            ) from exc
        accepted = accept_invitation(db, token=token, actor=user, request=request)
    except Exception:
        # accept_invitation rolls back what it started; a refusal before that
        # (no free seat) must not leave the new account behind either.
        db.rollback()
        raise
    logger.info("INVITATION_SIGNUP | user=%s | invitation=%s", user.id, accepted.invitation_id)
    return user, accepted


# ===========================================================================
# Rejection, revocation, resend
# ===========================================================================

def reject_invitation(
    db: Session, *, token: str, actor: User, request: Any = None
) -> ResolvedInvitationParties:
    context = audit_service.context_from_request(request)
    invitation = _load_by_token(db, token=token)
    _assert_actor_matches(invitation=invitation, actor=actor)

    organization = invitation.organization
    inviter = user_crud.get_user_by_id(db, user_id=invitation.inviter_id)

    try:
        claimed_id = invitation_crud.claim_invitation(
            db,
            token_hash=hash_token(token),
            new_status=InvitationStatus.REJECTED,
            now=datetime.now(UTC),
        )
        if claimed_id is None:
            raise _classify_claim_failure(invitation)

        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=actor.id,
            resource_type=AuditResourceType.INVITATION,
            resource_id=invitation.id,
            action=AuditAction.DECLINED,
            details={
                **audit_service.actor_snapshot(actor),
                "recipient_email": invitation.email,
            },
            **context,
        )

        commit_and_refresh(db, invitation)

        return ResolvedInvitationParties(
            organization_name=organization.name,
            organization_slug=organization.slug,
            inviter_email=inviter.email if inviter else "",
            inviter_display=_display_name(inviter),
            invited_display=_display_name(actor, invitation.email),
            invited_email=invitation.email,
            invitation_id=invitation.id,
        )

    except Exception as exc:
        rollback_and_log_error(
            db, logger, "Failed to reject invitation %s: %s",
            identity_of(invitation), str(exc), exc=exc,
        )


def revoke_invitation(
    db: Session,
    *,
    organization: Organization,
    invitation_id: uuid.UUID,
    actor: User,
    actor_role: OrganizationRole,
    request: Any = None,
) -> ResolvedInvitationParties:
    context = audit_service.context_from_request(request)

    if not can_invite_members(actor_role):
        raise InvitationPermissionDeniedError(
            "You do not have permission to manage invitations."
        )

    invitation = invitation_crud.get_invitation_by_id(
        db, invitation_id=invitation_id
    )
    if invitation is None or invitation.organization_id != organization.id:
        raise InvitationNotFoundError("Invitation not found.")

    inviter = user_crud.get_user_by_id(db, user_id=invitation.inviter_id)

    try:
        revoked_id = invitation_crud.revoke_invitation(
            db,
            invitation_id=invitation.id,
            revoked_by_id=actor.id,
            now=datetime.now(UTC),
        )
        if revoked_id is None:
            raise InvitationAlreadyProcessedError(
                f"This invitation was already "
                f"{invitation.status.value.lower()}."
            )

        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=actor.id,
            resource_type=AuditResourceType.INVITATION,
            resource_id=invitation.id,
            action=AuditAction.REVOKED,
            details={
                **audit_service.actor_snapshot(actor),
                "recipient_email": invitation.email,
            },
            **context,
        )

        commit_and_refresh(db, invitation)

        return ResolvedInvitationParties(
            organization_name=organization.name,
            organization_slug=organization.slug,
            inviter_email=inviter.email if inviter else "",
            inviter_display=_display_name(inviter),
            invited_display=invitation.email,
            invited_email=invitation.email,
            invitation_id=invitation.id,
        )

    except Exception as exc:
        rollback_and_log_error(
            db, logger, "Failed to revoke invitation %s: %s",
            identity_of(invitation), str(exc), exc=exc,
        )


def resend_invitation(
    db: Session,
    *,
    organization: Organization,
    invitation_id: uuid.UUID,
    actor_role: OrganizationRole,
    request: Any = None,
) -> IssuedInvitation:
    context = audit_service.context_from_request(request)

    if not can_invite_members(actor_role):
        raise InvitationPermissionDeniedError(
            "You do not have permission to manage invitations."
        )

    invitation = invitation_crud.get_invitation_by_id(
        db, invitation_id=invitation_id
    )
    if invitation is None or invitation.organization_id != organization.id:
        raise InvitationNotFoundError("Invitation not found.")

    if invitation.status is not InvitationStatus.PENDING:
        raise InvitationAlreadyProcessedError(
            f"This invitation was already {invitation.status.value.lower()}."
        )

    now = datetime.now(UTC)
    cooldown = timedelta(minutes=settings.INVITATION_RESEND_COOLDOWN_MINUTES)
    if invitation.last_sent_at and (now - invitation.last_sent_at) < cooldown:
        raise InvitationResendTooSoonError(
            f"This invitation was sent recently. Try again in a few minutes."
        )

    _assert_seat_available(db, organization=organization, already_reserved=True)

    inviter = user_crud.get_user_by_id(db, user_id=invitation.inviter_id)
    plaintext = generate_secure_token()

    try:
        invitation_crud.rotate_token(
            db,
            invitation=invitation,
            token_hash=hash_token(plaintext),
            expires_at=now + timedelta(hours=settings.INVITATION_TTL_HOURS),
            now=now,
        )

        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=inviter.id if inviter else None,
            resource_type=AuditResourceType.INVITATION,
            resource_id=invitation.id,
            action=AuditAction.UPDATED,
            details={
                **audit_service.actor_snapshot(inviter),
                "reason": "RESENT",
                "recipient_email": invitation.email,
                "send_count": invitation.send_count,
            },
            **context,
        )

        commit_and_refresh(db, invitation)

        return IssuedInvitation(
            invitation=invitation,
            plaintext_token=plaintext,
            organization_name=organization.name,
            inviter_email=inviter.email if inviter else "",
            inviter_display=_display_name(inviter),
            grant_lines=[
                GrantLine(g.workspace.workspace_name, g.role.value)
                for g in invitation.grants
                if g.workspace is not None
            ],
        )

    except Exception as exc:
        rollback_and_log_error(
            db, logger, "Failed to resend invitation %s: %s",
            identity_of(invitation), str(exc), exc=exc,
        )


def list_invitations(
    db: Session,
    *,
    organization_id: uuid.UUID,
    statuses: list[InvitationStatus] | None = None,
) -> list[OrganizationInvitation]:
    return invitation_crud.list_invitations_for_organization(
        db, organization_id=organization_id, statuses=statuses,
    )


def list_invitations_for_user(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> list[OrganizationInvitation]:
    user = user_crud.get_user_by_id(db, user_id=user_id)
    if user is None:
        return []
    # F-032. Registration never says whether an address is taken, so anyone can
    # hold an unverified account for someone else's address. Listing by the
    # address string alone showed that person the victim's pending invitations
    # (organization, role, inviter, workspaces). Only an account that has proved
    # the mailbox may read them. Accepting an invitation, which needs the emailed
    # token, still verifies the address, so a genuine invitee loses nothing.
    if user.email_verified_at is None:
        return []
    return invitation_crud.list_pending_invitations_for_email(db, email=user.email)


# ===========================================================================
# Sweep — consumed by Step 8
# ===========================================================================

def sweep_expired_invitations(
    db: Session, *, commit: bool = True
) -> dict[uuid.UUID, ExpiryDigestBatch]:
    rows = invitation_crud.expire_stale_invitations(db, now=datetime.now(UTC))
    if not rows:
        return {}

    for row in rows:
        inviter_user = user_crud.get_user_by_id(db, user_id=row["inviter_id"])
        audit_service.record(
            db,
            organization_id=row["organization_id"],
            actor_id=row["inviter_id"],
            resource_type=AuditResourceType.INVITATION,
            resource_id=row["id"],
            action=AuditAction.REVOKED,
            details={
                **audit_service.actor_snapshot(inviter_user),
                "reason": "EXPIRED",
                "recipient_email": row["email"],
            },
        )

    if commit:
        commit_and_refresh(db)

    inviter_ids = {r["inviter_id"] for r in rows}
    org_ids = {r["organization_id"] for r in rows}

    inviters = {
        u.id: u.email
        for u in db.execute(
            select(User).where(User.id.in_(inviter_ids))
        ).scalars()
    }
    organizations = {
        o.id: (o.name, o.slug)
        for o in db.execute(
            select(Organization).where(Organization.id.in_(org_ids))
        ).scalars()
    }

    batches: dict[uuid.UUID, ExpiryDigestBatch] = {}
    for row in rows:
        inviter_email = inviters.get(row["inviter_id"])
        if inviter_email is None:
            logger.warning(
                "INVITATION_SWEEP_ORPHAN | invitation=%s | inviter=%s",
                row["id"], row["inviter_id"],
            )
            continue

        org_name, org_slug = organizations.get(
            row["organization_id"], ("(unknown organization)", "")
        )
        batch = batches.setdefault(
            row["inviter_id"],
            ExpiryDigestBatch(
                inviter_email=inviter_email,
                organization_slug=org_slug,
                lines=[],
            ),
        )
        batch.lines.append(ExpiredInvitationLine(
            invited_email=row["email"],
            organization_name=org_name,
            expired_at_display=format_timestamp(row["expires_at"]),
        ))

    logger.info(
        "INVITATION_SWEEP | expired=%s | inviters=%s", len(rows), len(batches)
    )
    return batches


def purge_old_invitations(db: Session, *, commit: bool = True) -> int:
    cutoff = datetime.now(UTC) - timedelta(
        days=settings.INVITATION_RETENTION_DAYS
    )
    deleted = invitation_crud.delete_invitations_before(db, cutoff=cutoff)
    if deleted and commit:
        commit_and_refresh(db)
    return deleted
