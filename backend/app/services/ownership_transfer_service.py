"""
Ownership transfer orchestration for FlowPilot AI (ARCH-05 Step 6).

Four operations on one proposal: propose, accept, decline, cancel. Every one
of them is a state transition on an `OwnershipTransfer` row, and every state
transition is claimed with the same atomic conditional UPDATE primitive
(`ownership_transfer_crud.update_transfer_status`) that
`organization_invitation_service.claim_invitation` already established for
this codebase — a single `UPDATE ... WHERE status = 'PENDING' ... RETURNING`
statement, correct under concurrency regardless of isolation level, with no
read-then-write window for two requests to race through.

ACCEPT DOES NOT REIMPLEMENT PROMOTION. It calls
`organization_member_service.transfer_ownership` verbatim — the same
function the pre-ARCH-05, single-phase endpoint already called. That
function's own lock, its own re-check of `can_transfer_ownership` against
the initiator's CURRENT (not cached) role, and its own guards against
self-transfer and an inactive target are what make `accept_transfer` safe
against everything that could have changed in the up-to-seven-day gap
between initiation and acceptance — not code duplicated here.

CARRIERS, NOT ORM OBJECTS, CROSS THE COMMIT BOUNDARY. Every public function
below returns a frozen dataclass of primitives (uuids, strings, datetimes) —
the `AcceptedInvitation` pattern from `organization_invitation_service.py`,
applied here. Step 7 dispatches `ownership_mail.send_*` from a
`BackgroundTasks` callback that runs AFTER the request's session has closed;
an ORM object handed to that callback would be detached and unusable, and a
carrier is not.

════════════════════════════════════════════════════════════════════════════
A LIVE GAP THIS STEP DOES NOT CLOSE ON ITS OWN
════════════════════════════════════════════════════════════════════════════
`POST /organizations/{id}/transfer-ownership` (app/api/v1/organizations.py)
still exists, still requires nothing but `RequireOrgOwner`, and still calls
`transfer_ownership` directly with no consent, no re-authentication, and no
7-day window for the target to weigh anything. Every protection this file
adds is reachable ONLY through routes Step 7 has not built yet; the old
route bypasses all of it today. Recommendation for Step 7: delete that
endpoint. `transfer_ownership` itself stays exactly as reusable as it is now
— `accept_transfer` below is proof of that — only the unmediated direct
route needs to go.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    CannotTransferToSelfError,
    OrganizationMemberError,
    OrganizationPermissionDeniedError,
    PendingTransferExistsError,
    ReauthenticationFailedError,
    TargetNotVerifiedError,
    TransferExpiredError,
    TransferInitiatorMismatchError,
    TransferNotFoundError,
    TransferNotPendingError,
    TransferTargetMismatchError,
)
from app.core.links import build_ownership_transfer_link
from app.core.organization_permissions import can_transfer_ownership
from app.core.security import verify_password
from app.core.transactions import commit_and_refresh, rollback_and_log_error
from app.crud import ownership_transfer as transfer_crud
from app.crud import organization_members as organization_members_crud
from app.crud import user as user_crud
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
)
from app.models.ownership_transfer import OwnershipTransfer, OwnershipTransferStatus
from app.models.user import User
from app.services.organization_member_service import (
    get_membership_or_raise,
    lock_organization_for_owner_change,
    transfer_ownership,
)

logger = logging.getLogger("app.services.ownership_transfer_service")


def _display_name(user: User) -> str:
    """
    `display_name` if set, `email` otherwise. The single fallback point every
    carrier below goes through, matching the User model's own documented
    convention: "a NULL that a caller renders as the email address is
    honest... every read site is expected to fall back to email."
    """
    return user.display_name or user.email


# ============================================================================
# Carriers — what each operation produces, for Step 7's mail dispatch
# ============================================================================

@dataclass(frozen=True)
class InitiatedTransfer:
    """What initiation produced, and what the target needs to be told."""
    transfer_id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    organization_slug: str
    initiator_email: str
    initiator_display: str
    target_email: str
    target_display: str
    review_link: str
    expires_at: datetime


@dataclass(frozen=True)
class AcceptedTransfer:
    """
    What acceptance produced. Feeds BOTH perspectives of
    `ownership_mail.send_ownership_transferred` — one carrier, two mails.
    """
    transfer_id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    previous_owner_email: str
    previous_owner_display: str
    new_owner_email: str
    new_owner_display: str
    transferred_at: datetime


@dataclass(frozen=True)
class DeclinedTransfer:
    """What a decline produced, and what the initiator needs to be told."""
    transfer_id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    initiator_email: str
    target_email: str
    target_display: str
    declined_at: datetime


@dataclass(frozen=True)
class CancelledTransfer:
    """What a cancellation produced, and what the target needs to be told."""
    transfer_id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    initiator_email: str
    initiator_display: str
    target_email: str
    cancelled_at: datetime


# ============================================================================
# Shared claim helper
# ============================================================================

def _claim_or_raise(
    db: Session,
    *,
    transfer: OwnershipTransfer,
    new_status: OwnershipTransferStatus,
    now: datetime,
) -> None:
    """
    The shared body of accept/decline/cancel's first real write.

    §B.8: lazy expiry, no sweeper. This is checked FIRST, uniformly, across
    all three resolution paths — declining or cancelling something that has
    silently outlived its window gets "this has expired" rather than a
    successful-looking resolution of a proposal that, by clock time, no
    longer existed. If `transfer.expires_at` has passed, this claims EXPIRED
    instead of the caller's requested status and raises TransferExpiredError
    — this is the ONLY place `OwnershipTransferStatus.EXPIRED` is ever
    written (see the model's own docstring).

    Either claim attempt can lose a race to a concurrent request resolving
    the same row first; when it does, this raises TransferNotPendingError
    without asserting which status won, on the same reasoning
    `claim_invitation` gives for its own callers: "The caller classifies the
    failure with a second query, on the failure path only" — and here, no
    caller needs to.

    Raises:
        TransferExpiredError: expires_at had passed, and the EXPIRED claim
            (this call's own) succeeded.
        TransferNotPendingError: the row was no longer PENDING by the time
            either claim attempt ran — already resolved by a prior or
            concurrent call.
    """
    if transfer.expires_at <= now:
        claimed = transfer_crud.update_transfer_status(
            db,
            transfer_id=transfer.id,
            new_status=OwnershipTransferStatus.EXPIRED,
            now=now,
        )
        if claimed is not None:
            # Committed HERE, not left for the caller. Every caller of this
            # helper wraps its own body in `except Exception:
            # rollback_and_log_error(...)`, which exists to undo a GENUINELY
            # failed operation — but EXPIRED is not a failure, it is a
            # complete, valid, already-successful state transition that
            # happens to be SIGNALED by raising. Left uncommitted, the
            # caller's own broad except-and-rollback (correctly designed to
            # undo a failed accept/decline/cancel) would undo this write
            # too, since Python cannot tell "an exception carrying a
            # successful outcome" from "an exception carrying a failure" by
            # type alone once both are subclasses of Exception. Committing
            # before raising is what keeps the two from colliding.
            db.commit()
            raise TransferExpiredError(
                "This ownership transfer proposal has expired."
            )
        raise TransferNotPendingError(
            "This ownership transfer is no longer pending."
        )

    claimed = transfer_crud.update_transfer_status(
        db, transfer_id=transfer.id, new_status=new_status, now=now
    )
    if claimed is None:
        raise TransferNotPendingError(
            "This ownership transfer is no longer pending."
        )


# ============================================================================
# Initiate
# ============================================================================

def initiate_transfer(
    db: Session,
    *,
    organization: Organization,
    actor: User,
    initiator_membership: OrganizationMember,
    target_membership_id: uuid.UUID,
    current_password: str,
) -> InitiatedTransfer:
    """
    Proposes an ownership transfer. Nothing changes about who owns the
    organization until the target accepts (§B.1).

    Order of checks, and why it is this order:

    1. Password re-authentication (§B.2) runs FIRST, before any lock. Bcrypt
       verification is deliberately slow; holding `FOR UPDATE` on the
       organization row while it runs would serialize every OTHER owner-set
       operation on this tenant behind however long hashing takes. A wrong
       password also fails fastest this way — no lock is ever taken for a
       request that cannot succeed.
    2. Target identity, self-transfer, verification (A.2.3), and active-
       membership checks all run before the lock too — none of them read a
       ROLE to make a decision, so none of them need owner-set staleness
       protection. They read the target's identity, verification flag, and
       membership status, none of which the lock exists to guard.
    3. THE LOCK is taken immediately before the one check that DOES read a
       role: `can_transfer_ownership(initiator_membership.role)`. This is
       the ARCH-05 Step 1 discipline applied to a fifth caller of
       `lock_organization_for_owner_change` — imported transitively here via
       `transfer_ownership`'s own use of it is NOT what protects this
       function; initiation does not call `transfer_ownership`, so this
       function takes the lock itself.
    4. The pending-transfer check and the INSERT both run under that same
       lock. `uq_pending_ownership_transfer_per_org` is what actually
       enforces "at most one PENDING transfer" under concurrency; the
       pre-check exists only so two well-behaved sequential requests get a
       clear 409 rather than a raw `IntegrityError`.

    Raises:
        ReauthenticationFailedError: current_password does not match.
        OrganizationMemberError: target_membership_id does not resolve in
            this organization, or the target is not an ACTIVE member.
        CannotTransferToSelfError: the target is the initiator's own
            membership.
        TargetNotVerifiedError: the target has no email_verified_at (A.2.3).
        OrganizationPermissionDeniedError: the initiator is not currently an
            OWNER (re-checked under the lock, not trusted from OrgContext).
        PendingTransferExistsError: the organization already has a PENDING
            proposal.
    """
    if not verify_password(current_password, actor.hashed_password):
        logger.warning(
            "OWNERSHIP_TRANSFER_REAUTH_FAILED | Org: %s | User: %s",
            organization.id, actor.id,
        )
        raise ReauthenticationFailedError(
            "Your current password is incorrect. Re-enter your password to "
            "confirm this transfer."
        )

    target_membership = get_membership_or_raise(
        db, organization_id=organization.id, membership_id=target_membership_id
    )

    if target_membership.id == initiator_membership.id:
        raise CannotTransferToSelfError("You already own this organization.")

    if target_membership.status is not MembershipStatus.ACTIVE:
        raise OrganizationMemberError(
            "Ownership can only be transferred to an active member."
        )

    if target_membership.user.email_verified_at is None:
        raise TargetNotVerifiedError(
            "This member has not verified their email address. They must "
            "verify their address before ownership can be transferred to "
            "them."
        )

    # ARCH-05 Step 6 / Step 1 discipline. First read of a ROLE in this
    # function, so the lock precedes it — not the function's first
    # statement, because nothing before this line reads owner-set state.
    lock_organization_for_owner_change(
        db,
        organization_id=organization.id,
        refresh=(initiator_membership,),
    )

    if not can_transfer_ownership(initiator_membership.role):
        raise OrganizationPermissionDeniedError(
            "Only an organization owner can propose an ownership transfer."
        )

    existing = transfer_crud.get_pending_transfer_for_org(
        db, organization_id=organization.id
    )
    if existing is not None:
        raise PendingTransferExistsError(
            "This organization already has a pending ownership transfer. "
            "Cancel it before proposing a new one."
        )

    try:
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=settings.OWNERSHIP_TRANSFER_TTL_DAYS)

        transfer = transfer_crud.create_transfer(
            db,
            organization_id=organization.id,
            initiated_by_id=actor.id,
            target_membership_id=target_membership.id,
            expires_at=expires_at,
        )
        commit_and_refresh(db, transfer)

        logger.info(
            "AUDIT | OWNERSHIP_TRANSFER_INITIATED | Org: %s | Transfer: %s | "
            "From: %s | To: %s | Expires: %s",
            organization.id, transfer.id, actor.id,
            target_membership.user_id, expires_at,
        )

        return InitiatedTransfer(
            transfer_id=transfer.id,
            organization_id=organization.id,
            organization_name=organization.name,
            organization_slug=organization.slug,
            initiator_email=actor.email,
            initiator_display=_display_name(actor),
            target_email=target_membership.user.email,
            target_display=_display_name(target_membership.user),
            review_link=build_ownership_transfer_link(organization.slug),
            expires_at=expires_at,
        )

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to initiate ownership transfer for organization %s: %s",
            organization.id,
            str(exc),
            exc=exc,
        )


# ============================================================================
# Accept
# ============================================================================

def accept_transfer(
    db: Session,
    *,
    organization: Organization,
    transfer_id: uuid.UUID,
    actor: User,
) -> AcceptedTransfer:
    """
    Accepts a pending proposal. Promotes the target to OWNER and demotes the
    initiator to ADMIN, by calling `transfer_ownership` verbatim.

    IDENTITY CHECK DELIBERATELY COMPARES `actor.id` TO
    `target_membership.user_id`, NOT EMAIL. `TransferTargetMismatchError`'s
    own docstring explains why this does not follow
    `_assert_actor_matches`'s email-comparison precedent: `target_membership_id`
    is a foreign key to an EXISTING, VERIFIED membership (A.2.3 required
    verification before this proposal could even be created), so there is no
    unproven-address ambiguity for an identifier comparison to route around,
    and an identifier comparison is strictly more precise than string-
    comparing an already-immutable (§B.5) email would be.

    A CLAIMED-THEN-ROLLED-BACK ACCEPT REVERTS TO PENDING, ON PURPOSE. If the
    conditional UPDATE below claims ACCEPTED and `transfer_ownership` then
    raises (the initiator lost owner status in the interim; the target was
    deactivated in the interim), this function's own `except` block rolls
    back the ENTIRE transaction — including the claim — leaving the
    transfer PENDING again. This is not a gap; it is the same behavior
    `claim_invitation` documents for itself: "If the caller then fails... the
    claim rolls back with it and the link still works." A transfer that
    cannot be accepted right now stays available to retry, or lazily expires
    at its original 7-day deadline regardless.

    Raises:
        TransferNotFoundError: transfer_id does not resolve in this
            organization.
        TransferTargetMismatchError: actor is not this proposal's target.
        TransferExpiredError / TransferNotPendingError: see _claim_or_raise.
        OrganizationPermissionDeniedError: the initiator's membership no
            longer exists, or (via transfer_ownership) they are no longer an
            OWNER.
        OrganizationMemberError: (via transfer_ownership) the target is no
            longer an active member.
    """
    transfer = transfer_crud.get_transfer_by_id(
        db, organization_id=organization.id, transfer_id=transfer_id
    )
    if transfer is None:
        raise TransferNotFoundError("No such ownership transfer.")

    target_membership = get_membership_or_raise(
        db,
        organization_id=organization.id,
        membership_id=transfer.target_membership_id,
    )

    if actor.id != target_membership.user_id:
        raise TransferTargetMismatchError(
            "This ownership transfer was not proposed to you."
        )

    if transfer.status is not OwnershipTransferStatus.PENDING:
        raise TransferNotPendingError(
            "This ownership transfer is no longer pending."
        )

    try:
        now = datetime.now(UTC)

        # The claim. Flushed, not committed — transfer_ownership's own
        # commit_and_refresh below is the single commit point for both this
        # write and its promote/demote writes, atomically.
        _claim_or_raise(
            db,
            transfer=transfer,
            new_status=OwnershipTransferStatus.ACCEPTED,
            now=now,
        )

        initiator_membership = organization_members_crud.get_organization_member(
            db, organization_id=organization.id, user_id=transfer.initiated_by_id
        )
        if initiator_membership is None:
            raise OrganizationPermissionDeniedError(
                "The organization owner who proposed this transfer is no "
                "longer a member. Ask a current owner to propose a new "
                "transfer."
            )

        previous_owner_email = initiator_membership.user.email
        previous_owner_display = _display_name(initiator_membership.user)
        new_owner_email = target_membership.user.email
        new_owner_display = _display_name(target_membership.user)

        # ARCH-05 Step 6 / §B.1. Reused verbatim, not reimplemented. Its own
        # lock, its own re-check of can_transfer_ownership against the
        # initiator's CURRENT role, its own self-transfer and active-target
        # guards, and its own commit all apply here unchanged.
        transfer_ownership(
            db,
            organization=organization,
            current_owner_membership=initiator_membership,
            target_membership=target_membership,
        )

        logger.info(
            "AUDIT | OWNERSHIP_TRANSFER_ACCEPTED | Org: %s | Transfer: %s | "
            "From: %s | To: %s",
            organization.id, transfer.id,
            initiator_membership.user_id, target_membership.user_id,
        )

        return AcceptedTransfer(
            transfer_id=transfer.id,
            organization_id=organization.id,
            organization_name=organization.name,
            previous_owner_email=previous_owner_email,
            previous_owner_display=previous_owner_display,
            new_owner_email=new_owner_email,
            new_owner_display=new_owner_display,
            transferred_at=now,
        )

    except TransferExpiredError:
        # Not a failure. _claim_or_raise already committed this outcome
        # before raising, on purpose — see its own docstring. db.rollback()
        # here is a genuine no-op (there is nothing pending left to undo),
        # kept only so the session is in a known-clean state before the
        # exception reaches the caller. rollback_and_log_error's
        # logger.exception + traceback is the wrong tool for a routine,
        # already-correctly-persisted business outcome.
        db.rollback()
        raise
    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to accept ownership transfer %s for organization %s: %s",
            transfer_id,
            organization.id,
            str(exc),
            exc=exc,
        )


# ============================================================================
# Decline
# ============================================================================

def decline_transfer(
    db: Session,
    *,
    organization: Organization,
    transfer_id: uuid.UUID,
    actor: User,
) -> DeclinedTransfer:
    """
    Declines a pending proposal. The initiator keeps their ownership; the
    target keeps whatever role they already had. §B.7: otherwise a declined
    proposal looks identical to an ignored one to the person who sent it.

    Raises:
        TransferNotFoundError, TransferTargetMismatchError,
        TransferExpiredError, TransferNotPendingError: see accept_transfer
        and _claim_or_raise.
    """
    transfer = transfer_crud.get_transfer_by_id(
        db, organization_id=organization.id, transfer_id=transfer_id
    )
    if transfer is None:
        raise TransferNotFoundError("No such ownership transfer.")

    target_membership = get_membership_or_raise(
        db,
        organization_id=organization.id,
        membership_id=transfer.target_membership_id,
    )

    if actor.id != target_membership.user_id:
        raise TransferTargetMismatchError(
            "This ownership transfer was not proposed to you."
        )

    if transfer.status is not OwnershipTransferStatus.PENDING:
        raise TransferNotPendingError(
            "This ownership transfer is no longer pending."
        )

    try:
        now = datetime.now(UTC)
        _claim_or_raise(
            db,
            transfer=transfer,
            new_status=OwnershipTransferStatus.DECLINED,
            now=now,
        )
        commit_and_refresh(db, transfer)

        # The initiator's USER row, not their membership. Their membership
        # in this org may legitimately be gone by now (they left, or were
        # removed) and that is not a reason to fail a decline — the target
        # is allowed to decline regardless. Their USER row is guaranteed to
        # still exist: initiated_by_id -> users.id cascades on delete (Step
        # 4), so if this transfer row still exists, so does the user it
        # names.
        initiator = user_crud.get_user_by_id(db, user_id=transfer.initiated_by_id)

        logger.info(
            "AUDIT | OWNERSHIP_TRANSFER_DECLINED | Org: %s | Transfer: %s | "
            "By: %s",
            organization.id, transfer.id, actor.id,
        )

        return DeclinedTransfer(
            transfer_id=transfer.id,
            organization_id=organization.id,
            organization_name=organization.name,
            initiator_email=initiator.email,
            target_email=target_membership.user.email,
            target_display=_display_name(target_membership.user),
            declined_at=now,
        )

    except TransferExpiredError:
        # See accept_transfer's identical block: not a failure, already
        # committed by _claim_or_raise, and rollback here is a no-op kept
        # only for session hygiene.
        db.rollback()
        raise
    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to decline ownership transfer %s for organization %s: %s",
            transfer_id,
            organization.id,
            str(exc),
            exc=exc,
        )


# ============================================================================
# Cancel
# ============================================================================

def cancel_transfer(
    db: Session,
    *,
    organization: Organization,
    transfer_id: uuid.UUID,
    actor: User,
) -> CancelledTransfer:
    """
    Withdraws a pending proposal. Initiator-only: a different current owner
    may not cancel a proposal they did not make, even one from an outgoing
    owner who has since lost the role — §B.8's lazy expiry is the intended
    resolution for that case, not a broadened cancel permission (see
    TransferInitiatorMismatchError's docstring).

    Raises:
        TransferNotFoundError: transfer_id does not resolve in this
            organization.
        TransferInitiatorMismatchError: actor did not propose this transfer.
        TransferExpiredError / TransferNotPendingError: see _claim_or_raise.
    """
    transfer = transfer_crud.get_transfer_by_id(
        db, organization_id=organization.id, transfer_id=transfer_id
    )
    if transfer is None:
        raise TransferNotFoundError("No such ownership transfer.")

    if actor.id != transfer.initiated_by_id:
        raise TransferInitiatorMismatchError(
            "Only the person who proposed this transfer can cancel it."
        )

    if transfer.status is not OwnershipTransferStatus.PENDING:
        raise TransferNotPendingError(
            "This ownership transfer is no longer pending."
        )

    try:
        now = datetime.now(UTC)
        _claim_or_raise(
            db,
            transfer=transfer,
            new_status=OwnershipTransferStatus.CANCELLED,
            now=now,
        )
        commit_and_refresh(db, transfer)

        target_membership = get_membership_or_raise(
            db,
            organization_id=organization.id,
            membership_id=transfer.target_membership_id,
        )

        logger.info(
            "AUDIT | OWNERSHIP_TRANSFER_CANCELLED | Org: %s | Transfer: %s | "
            "By: %s",
            organization.id, transfer.id, actor.id,
        )

        return CancelledTransfer(
            transfer_id=transfer.id,
            organization_id=organization.id,
            organization_name=organization.name,
            initiator_email=actor.email,
            initiator_display=_display_name(actor),
            target_email=target_membership.user.email,
            cancelled_at=now,
        )

    except TransferExpiredError:
        # See accept_transfer's identical block: not a failure, already
        # committed by _claim_or_raise, and rollback here is a no-op kept
        # only for session hygiene.
        db.rollback()
        raise
    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to cancel ownership transfer %s for organization %s: %s",
            transfer_id,
            organization.id,
            str(exc),
            exc=exc,
        )