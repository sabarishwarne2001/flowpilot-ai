"""
Business orchestration for the ARCH-04 invitation lifecycle.

Owns the transaction boundary for issuance, acceptance, rejection,
revocation, and resend. Authorization delegates to
app.core.organization_permissions; persistence to app.crud.

THIS MODULE SENDS NO MAIL. Every mutating function returns a frozen carrier
built from committed state, and Step 7's router dispatches it through
BackgroundTasks. Step 1's invitation_mail requires that notices go out AFTER
the transaction resolves; returning a carrier makes that a structural
guarantee rather than a comment, since there is nothing to dispatch until the
commit has happened. It also keeps every test below runnable with no SMTP
configuration, and — because invitation_mail takes no Session — the background
task cannot inherit the stale-session hazard of the ARCH-03 register path.

THE TWO CHECKS THAT MATTER MOST
-------------------------------
1. Grant tenancy (§D6.4). Every workspace named in a grant must belong to the
   inviting organization. Without this an ADMIN of organization A can attach a
   workspace from organization B and acceptance provisions a WorkspaceMember
   row across the tenant boundary — a cross-tenant escalation delivered by
   invitation. The whole request is rejected rather than the grant filtered:
   naming a foreign workspace is a bug or an attack, and silently dropping it
   conceals both.

2. Seat accounting (§0). A pending invitation reserves a seat but writes no
   OrganizationMember row, so count_consumed_seats alone under-reports.
   count_reserved_seats below is the only correct figure and the only one any
   ceiling is enforced against.
"""

from __future__ import annotations

import logging
import uuid
import sqlalchemy as sa
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    InvalidInvitationTokenError,
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
    SeatLimitExceededError,
)
from app.core.links import build_invitation_accept_link
from app.core.organization_permissions import (
    can_assign_organization_role,
    can_invite_members,
)
from app.core.tokens import generate_secure_token, hash_token
from app.core.transactions import commit_and_refresh, rollback_and_log_error
from app.crud import organization_invitation as invitation_crud
from app.crud import organization_members as organization_members_crud
from app.crud import user as user_crud
from app.crud import workspace as workspace_crud
from app.crud import workspace_members as workspace_members_crud
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
    #: §B.6. Prose only — never an href. See _display_name.
    inviter_display: str
    grant_lines: list[GrantLine]

    @property
    def accept_link(self) -> str:
        return build_invitation_accept_link(self.plaintext_token)


def _display_name(user: User | None, fallback: str = "") -> str:
    """
    A person's display_name, falling back to their address (ARCH-05 §B.4/§B.6).

    THE SINGLE FALLBACK POINT for this service. `users.display_name` is
    nullable and a NULL is honest — the User model's own docstring is
    explicit that "every read site is expected to fall back to `email`, not
    to invent a name" — so the fallback has to live somewhere, and one
    helper is what keeps it from being reimplemented five slightly different
    ways across the carriers and audit lines below.

    Returns `fallback` for a missing user. Every caller here already treats
    a deleted inviter as recoverable (`inviter.email if inviter else ""`),
    and this preserves that rather than introducing a new failure mode into
    a courtesy notice.
    """
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
    message: str,
) -> None:
    if organization.seat_limit is None:
        return

    reserved = count_reserved_seats(db, organization_id=organization.id)
    if reserved >= organization.seat_limit:
        raise SeatLimitExceededError(message)


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
) -> IssuedInvitation:
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

    if invitation_crud.get_pending_invitation_for_email(
        db, organization_id=organization.id, email=normalized
    ) is not None:
        raise InvitationAlreadyExistsError(
            "An invitation to this address is already outstanding. Revoke it "
            "first, or resend it."
        )

    grant_lines = _resolve_grants(
        db, organization=organization, requested=grants
    )

    _assert_seat_available(
        db,
        organization=organization,
        message=(
            f"{organization.name} has no seats available. Free a seat or "
            f"raise the limit before inviting."
        ),
    )

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
        commit_and_refresh(db, invitation)

        logger.info(
            "AUDIT | INVITATION_ISSUED | Org: %s | Invitation: %s | "
            "To: %s | Role: %s | Grants: %s | Actor: %s (%s)",
            organization.id, invitation.id, normalized,
            organization_role.value, len(grants), inviter.id,
            _display_name(inviter, inviter.email),
        )

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
            organization.id, str(exc), exc=exc,
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
) -> AcceptedInvitation:
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
            organization_members_crud.create_organization_member(
                db,
                organization_id=organization.id,
                user_id=actor.id,
                role=applied_role,
                status=MembershipStatus.ACTIVE,
            )
        else:
            db.refresh(membership)

            if (
                membership.role is OrganizationRole.OWNER
                and membership.status is MembershipStatus.ACTIVE
            ):
                applied_role = OrganizationRole.OWNER
                role_preserved = True
            else:
                organization_members_crud.update_organization_member_role(
                    db, membership=membership, role=applied_role
                )

            organization_members_crud.set_organization_member_status(
                db, membership=membership, status=MembershipStatus.ACTIVE,
            )

        provisioned: list[GrantLine] = []
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

        if owners_before >= 1:
            owners_after = organization_members_crud.count_active_owners(
                db, organization_id=organization.id
            )
            if owners_after < 1:
                raise LastOwnerError(
                    f"Accepting this invitation would leave {organization.name} "
                    f"without an active owner."
                )

        if actor.email_verified_at is None:
            actor.email_verified_at = now
            db.add(actor)
            
            from app.models.auth_token import AuthToken, AuthTokenPurpose
            db.execute(
                sa.delete(AuthToken).where(
                    AuthToken.user_id == actor.id,
                    AuthToken.purpose == AuthTokenPurpose.EMAIL_VERIFICATION
                )
            )

        commit_and_refresh(db, invitation)

        logger.info(
            "AUDIT | INVITATION_ACCEPTED | Org: %s | Invitation: %s | "
            "User: %s | Role: %s | RolePreserved: %s | Provisioned: %s | "
            "Skipped: %s",
            organization.id, invitation.id, actor.id,
            applied_role.value, role_preserved, len(provisioned), skipped,
        )

        if role_preserved:
            logger.warning(
                "AUDIT | INVITATION_ROLE_NOT_APPLIED | Org: %s | "
                "Invitation: %s | User: %s | Requested: %s | Retained: OWNER",
                organization.id, invitation.id, actor.id,
                invitation.organization_role.value,
            )

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
        )

    except Exception as exc:
        rollback_and_log_error(
            db, logger,
            "Failed to accept invitation %s: %s",
            invitation.id, str(exc), exc=exc,
        )


# ===========================================================================
# Rejection, revocation, resend
# ===========================================================================

def reject_invitation(
    db: Session, *, token: str, actor: User
) -> ResolvedInvitationParties:
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

        commit_and_refresh(db, invitation)

        logger.info(
            "AUDIT | INVITATION_REJECTED | Org: %s | Invitation: %s",
            organization.id, invitation.id,
        )
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
            invitation.id, str(exc), exc=exc,
        )


def revoke_invitation(
    db: Session,
    *,
    organization: Organization,
    invitation_id: uuid.UUID,
    actor: User,
    actor_role: OrganizationRole,
) -> ResolvedInvitationParties:
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

        commit_and_refresh(db, invitation)

        logger.info(
            "AUDIT | INVITATION_REVOKED | Org: %s | Invitation: %s | Actor: %s",
            organization.id, invitation.id, actor.id,
        )
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
            invitation.id, str(exc), exc=exc,
        )


def resend_invitation(
    db: Session,
    *,
    organization: Organization,
    invitation_id: uuid.UUID,
    actor_role: OrganizationRole,
) -> IssuedInvitation:
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

    _assert_seat_available(
        db,
        organization=organization,
        message=(
            f"{organization.name} has no seats available. Free a seat before "
            f"resending this invitation."
        ),
    )

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
        commit_and_refresh(db, invitation)

        logger.info(
            "AUDIT | INVITATION_RESENT | Org: %s | Invitation: %s | Send: %s",
            organization.id, invitation.id, invitation.send_count,
        )

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
            invitation.id, str(exc), exc=exc,
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
    """
    Returns pending invitations issued to the user's email address.
    """
    user = user_crud.get_user_by_id(db, user_id=user_id)
    if user is None:
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