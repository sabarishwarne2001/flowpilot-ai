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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    InvalidInvitationTokenError,
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
from app.templates.emails.common import GrantLine

logger = logging.getLogger("app.services.organization_invitation")


# ===========================================================================
# Carriers
#
# Every one is built AFTER commit and carries only primitives. See the module
# docstring: this is what makes "notices go out after the transaction" a
# property of the types rather than a rule someone has to remember.
# ===========================================================================

@dataclass(frozen=True)
class IssuedInvitation:
    """
    A new invitation plus the plaintext token that addresses it.

    Same contract as IssuedAuthToken and IssuedSession: the plaintext is
    returned, never persisted, and the caller must build the link before it
    goes out of scope.
    """
    invitation: OrganizationInvitation
    plaintext_token: str
    organization_name: str
    inviter_email: str
    grant_lines: list[GrantLine]

    @property
    def accept_link(self) -> str:
        return build_invitation_accept_link(self.plaintext_token)


@dataclass(frozen=True)
class AcceptedInvitation:
    """What acceptance produced, and what the inviter needs to be told."""
    invitation_id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    inviter_email: str
    invited_email: str
    organization_role: OrganizationRole
    provisioned_grants: list[GrantLine] = field(default_factory=list)
    skipped_grant_count: int = 0


@dataclass(frozen=True)
class ResolvedInvitationParties:
    """Addresses and names for a notice, resolved before the carrier is built."""
    organization_name: str
    inviter_email: str
    invited_email: str
    invitation_id: uuid.UUID


# ===========================================================================
# Seats — §0
# ===========================================================================

def count_reserved_seats(db: Session, *, organization_id: uuid.UUID) -> int:
    """
    Seats occupied or reserved: active members plus outstanding invitations.

    The ONLY correct seat figure under ARCH-04, and the only one any ceiling
    is enforced against. See §0 of the Step 6 design for why
    count_consumed_seats alone is not it.
    """
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
    """
    Raises SeatLimitExceededError unless the organization has room for one more.

    seat_limit IS NULL means unlimited (§B.8). Called at issuance and again at
    acceptance, because between the two someone else may have taken the last
    seat.
    """
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
    """
    Validates every requested grant against the inviting organization.

    THE cross-tenant check of this phase. Rejects the entire request if any
    workspace belongs to another organization, does not exist, or is not
    ACTIVE — see §D6.4 for why the whole request rather than the offending
    entry.

    Returns display lines for the invitation email. The caller writes the
    grant rows from `requested`; this function's job is to make sure it may.
    """
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

        # One message for "not yours" and "does not exist", deliberately. A
        # distinct error would confirm to an administrator of one tenant that
        # a workspace id belongs to another — the same enumeration-oracle
        # reasoning behind returning 404 rather than 403 on foreign tenants.
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
    """
    Issues an invitation to join an organization, with zero or more grants.

    Zero grants is a first-class case (§B.1): it is how a BILLING manager is
    onboarded, and it is why this phase exists.

    Raises:
        InvitationPermissionDeniedError: Actor may not invite, or may not
            assign this role.
        InvitationAlreadyMemberError: Address already holds a live membership.
        InvitationAlreadyExistsError: A PENDING invitation already exists.
        InvitationGrantError: A grant is not attachable (§D6.4).
        SeatLimitExceededError: No seat available.
    """
    grants = grants or []
    normalized = email.strip().lower()

    if not can_invite_members(actor_role):
        raise InvitationPermissionDeniedError(
            "You do not have permission to invite members to this organization."
        )

    # OWNER is excluded by ck_organization_invitations_role_not_owner as well;
    # checking here means the caller gets a readable 403 instead of an
    # IntegrityError surfaced as a 500.
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

    # §D6.7 — also what keeps the two seat counts in §0 disjoint.
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

        # Never the token, never the link — an application log is not a secret
        # store (ARCH-03 R4).
        logger.info(
            "AUDIT | INVITATION_ISSUED | Org: %s | Invitation: %s | "
            "To: %s | Role: %s | Grants: %s | Actor: %s",
            organization.id, invitation.id, normalized,
            organization_role.value, len(grants), inviter.id,
        )

        return IssuedInvitation(
            invitation=invitation,
            plaintext_token=plaintext,
            organization_name=organization.name,
            inviter_email=inviter.email,
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
    """
    Resolves a plaintext token to its invitation, or raises.

    Reads terminal rows too, so the caller can say "already accepted" instead
    of "invalid link". Distinguishing these is safe: the token is 256 bits of
    randomness, so an attacker cannot produce a value landing in either
    bucket, and being told which bucket a value fell into reveals nothing
    actionable. Same reasoning as _classify_consumption_failure in ARCH-03.
    """
    invitation = invitation_crud.get_invitation_by_token_hash(
        db, token_hash=hash_token(token)
    )
    if invitation is None:
        raise InvalidInvitationTokenError("This invitation link is invalid.")
    return invitation


def _classify_claim_failure(invitation: OrganizationInvitation) -> Exception:
    """Works out why the conditional UPDATE matched nothing."""
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
    """
    The authorization check for accept and reject.

    The token identifies the invitation; the session identifies the actor.
    Before ARCH-01 both operations took only a token, so any holder could act
    on the invitee's behalf — and reject in particular gave a token holder a
    denial of service on the invitation.

    Deliberately compares session email to invitation email rather than
    consulting invited_user_id. That column is a stale binding written at
    issuance; using it to authorize would let an invitation verify an address
    nobody has proved control of.
    """
    if actor.email.strip().lower() != invitation.email.strip().lower():
        raise InvitationEmailMismatchError(
            f"This invitation was sent to {invitation.email}. Sign in with "
            f"that address to accept it."
        )


def preview_invitation(db: Session, *, token: str) -> dict:
    """
    Resolves an invitation for public display without mutating it.

    Served unauthenticated so a recipient can see what they are joining before
    creating an account. Read-only; every write operation below requires an
    actor.
    """
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
        "invited_email": invitation.email,
        "organization_role": invitation.organization_role,
        "workspaces": [
            {"name": g.workspace.workspace_name, "role": g.role}
            for g in invitation.grants
            if g.workspace is not None
        ],
        "expires_at": invitation.expires_at,
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
    """
    Accepts an invitation, provisioning a seat and every live grant atomically.

    ORDER INSIDE THE TRANSACTION IS LOAD-BEARING:

        1. seat check     — must precede the claim, so a seat failure leaves
                            the invitation PENDING and its token unspent (§B.8)
        2. claim (UPDATE) — must precede provisioning, so two racing requests
                            cannot both provision
        3. provisioning   — inside the same transaction, so a failure here
                            rolls the claim back and the link still works

    Raises:
        InvitationEmailMismatchError: The actor is not the invited party.
        InvitationAlreadyProcessedError / InvitationExpiredError.
        SeatLimitExceededError: Non-destructive — see §D6.2.
    """
    invitation = _load_by_token(db, token=token)
    _assert_actor_matches(invitation=invitation, actor=actor)

    organization = invitation.organization
    inviter = user_crud.get_user_by_id(db, user_id=invitation.inviter_id)
    now = datetime.now(UTC)

    # 1. Seat check FIRST. Nothing has been mutated at this point, so raising
    #    here leaves the invitation exactly as it was.
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
        # 2. Claim.
        claimed_id = invitation_crud.claim_invitation(
            db,
            token_hash=hash_token(token),
            new_status=InvitationStatus.ACCEPTED,
            now=now,
            actor_id=actor.id,
        )
        if claimed_id is None:
            raise _classify_claim_failure(invitation)

        # 3. Provision the seat. Reactivate rather than duplicate (§D6.8).
        membership = organization_members_crud.get_organization_member(
            db, organization_id=organization.id, user_id=actor.id
        )
        if membership is None:
            organization_members_crud.create_organization_member(
                db,
                organization_id=organization.id,
                user_id=actor.id,
                role=invitation.organization_role,
                status=MembershipStatus.ACTIVE,
            )
        else:
            organization_members_crud.update_organization_member_role(
                db, membership=membership, role=invitation.organization_role
            )
            organization_members_crud.set_organization_member_status(
                db, membership=membership, status=MembershipStatus.ACTIVE
            )

        # 4. Provision grants. A workspace deleted since issuance took its
        #    grant by cascade, so this list may already be shorter than what
        #    was issued; a workspace archived since is skipped here. Both are
        #    counted, not raised on (§B.2, R8).
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

        commit_and_refresh(db, invitation)

        logger.info(
            "AUDIT | INVITATION_ACCEPTED | Org: %s | Invitation: %s | "
            "User: %s | Role: %s | Provisioned: %s | Skipped: %s",
            organization.id, invitation.id, actor.id,
            invitation.organization_role.value, len(provisioned), skipped,
        )

        return AcceptedInvitation(
            invitation_id=invitation.id,
            organization_id=organization.id,
            organization_name=organization.name,
            inviter_email=inviter.email if inviter else "",
            invited_email=invitation.email,
            organization_role=invitation.organization_role,
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
    """
    Declines an invitation on behalf of the authenticated actor.

    Requires the actor, not just the token: reject-by-token gave any holder a
    denial of service on the invitation.
    """
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
            inviter_email=inviter.email if inviter else "",
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
    """
    Withdraws a pending invitation.

    The organization scope is not redundant: without it, an administrator of
    one tenant could revoke an invitation in another by supplying its id.
    """
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
            inviter_email=inviter.email if inviter else "",
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
    """
    Reissues a pending invitation with a fresh token and expiry (§D6.6).

    The previous link stops working. That is the point: a resend usually means
    the first mail never arrived, so the old token is dead weight, and an
    invitation grants organization membership — a higher-value credential than
    an email verification.

    Raises:
        InvitationResendTooSoonError: Inside the cooldown window.
    """
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

    # A resend re-checks seats: the organization may have filled up since the
    # invitation was issued, and re-mailing a link that cannot be accepted
    # sends the recipient into the §B.8 failure for no reason.
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


# ===========================================================================
# Sweep — consumed by Step 8
# ===========================================================================

def sweep_expired_invitations(db: Session) -> dict[uuid.UUID, list[dict]]:
    """
    Marks lapsed invitations EXPIRED and groups them by inviter for the digest.

    Returns {inviter_id: [row, ...]}. One message per inviter per run (§B.7);
    the sweeper must never iterate invitations.
    """
    rows = invitation_crud.expire_stale_invitations(db, now=datetime.now(UTC))
    if not rows:
        return {}

    commit_and_refresh(db)

    grouped: dict[uuid.UUID, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["inviter_id"], []).append(row)

    logger.info(
        "INVITATION_SWEEP | expired=%s | inviters=%s", len(rows), len(grouped)
    )
    return grouped


def purge_old_invitations(db: Session) -> int:
    """Deletes terminal invitations past the retention window. Grants cascade."""
    cutoff = datetime.now(UTC) - timedelta(
        days=settings.INVITATION_RETENTION_DAYS
    )
    deleted = invitation_crud.delete_invitations_before(db, cutoff=cutoff)
    if deleted:
        commit_and_refresh(db)
        logger.info("INVITATION_PURGE | deleted=%s", deleted)
    return deleted