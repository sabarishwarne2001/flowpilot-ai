"""
Domain exception taxonomy for FlowPilot AI.

Every business-level failure raised by the service layer inherits from
FlowPilotError. The global handler in app/core/exception_handlers.py resolves
each exception to an HTTP status code and a stable error code by walking the
class hierarchy, so new exception types inherit sensible behavior from their
base without touching the handler.

Layering note: this module has no imports. It sits below core utilities,
CRUD, services, and API, and may be imported freely by any of them.
"""

from __future__ import annotations


# ============================================================================
# Root
# ============================================================================

class FlowPilotError(Exception):
    """Base exception for every FlowPilot AI domain failure."""
    pass


# ============================================================================
# Organizations (commercial tenant)
# ============================================================================

class OrganizationError(FlowPilotError):
    """Base exception for all organization-related failures."""
    pass


class OrganizationNotFoundError(OrganizationError):
    """Raised when an organization record does not exist."""
    pass


class OrganizationAlreadyExistsError(OrganizationError):
    """Raised when attempting to register a duplicate organization."""
    pass


class OrganizationAccessDeniedError(OrganizationError):
    """
    Raised when the actor holds no active membership in the organization.

    Deliberately distinct from OrganizationPermissionDeniedError: this maps to
    404, not 403, so that a non-member cannot use the response to confirm that
    an organization exists.
    """
    pass


class OrganizationPermissionDeniedError(OrganizationError):
    """
    Raised when an active member's role is insufficient for the operation.

    Maps to 403. The actor already knows the organization exists, so there is
    no enumeration risk in acknowledging it.
    """
    pass


class OrganizationMemberError(OrganizationError):
    """Raised when an organization membership constraint is violated."""
    pass


class LastOwnerError(OrganizationMemberError):
    """
    Raised when an operation would leave an organization without an active
    owner, for example the final owner attempting to leave or self-demote.
    """
    pass


# ============================================================================
# Workspaces (collaboration boundary)
# ============================================================================

class WorkspaceError(FlowPilotError):
    """Base exception for all workspace-related failures."""
    pass


class WorkspaceNotFoundError(WorkspaceError):
    """Raised when a workspace record is missing or inactive."""
    pass


class WorkspaceAlreadyExistsError(WorkspaceError):
    """Raised when trying to register a duplicate workspace."""
    pass


class WorkspaceAccessDeniedError(WorkspaceError):
    """
    Raised when the actor holds no effective grant on the workspace.

    Maps to 404 rather than 403, for the same non-enumeration reason as
    OrganizationAccessDeniedError.
    """
    pass


class WorkspacePermissionDeniedError(WorkspaceError):
    """
    Raised when the actor's effective workspace role is insufficient.

    Maps to 403.
    """
    pass


class WorkspaceMemberError(WorkspaceError):
    """Raised when a membership constraint is violated."""
    pass


# ============================================================================
# Tenant status
# ============================================================================

class TenantSuspendedError(FlowPilotError):
    """
    Raised when an organization or workspace is suspended or archived and
    therefore cannot serve requests.

    Distinguished from access and permission failures so that the client can
    render an explanatory tombstone instead of a generic error.
    """
    pass


# ============================================================================
# Slugs (public tenant identifiers)
# ============================================================================

class SlugError(FlowPilotError):
    """Base exception for tenant identifier failures."""
    pass


class InvalidSlugError(SlugError):
    """Raised when a supplied identifier violates the slug grammar."""
    pass


class ReservedSlugError(SlugError):
    """Raised when a supplied identifier belongs to the platform namespace."""
    pass


class SlugUnavailableError(SlugError):
    """Raised when a unique identifier could not be allocated."""
    pass


# ============================================================================
# Invitations
# ============================================================================

class InvitationError(FlowPilotError):
    """Base exception for all workspace invitation operations."""
    pass


class InvitationNotFoundError(InvitationError):
    """Raised when an invitation record cannot be found."""
    pass


class InvitationExpiredError(InvitationError):
    """Raised when an invitation has passed its expiry timestamp."""
    pass


class InvitationPermissionDeniedError(InvitationError):
    """Raised when the actor lacks sufficient privileges."""
    pass


class InvitationEmailMismatchError(InvitationError):
    """
    Raised when the authenticated actor's email does not match the invited
    address.

    Distinct from InvitationPermissionDeniedError so the client can offer to
    sign out and switch accounts rather than showing a dead end. This check is
    what closes the pre-ARCH-01 hole in which any holder of an invitation token
    could accept it on the invitee's behalf.
    """
    pass


class InvitationAlreadyExistsError(InvitationError):
    """Raised when a pending invitation already exists."""
    pass


class InvitationAlreadyProcessedError(InvitationError):
    """Raised when an invitation is no longer pending."""
    pass


class InvitationAlreadyMemberError(InvitationError):
    """Raised when the invited user already belongs to the workspace."""
    pass


class InvalidInvitationTokenError(InvitationError):
    """Raised when an invitation token is invalid."""
    pass


class SeatLimitExceededError(InvitationError):
    """
    Raised when an organization has no seat available for a new member.

    Checked twice (ARCH-04 §B.8): at invitation issuance, so a full
    organization learns before an email goes out; and again at acceptance,
    because between the two someone else may have taken the last seat.

    The acceptance-side failure is deliberately non-destructive — the
    invitation remains PENDING and its token unconsumed — so the message this
    carries should read as recoverable rather than terminal. Maps to 409.
    """
    pass


class InvitationGrantError(InvitationError):
    """
    Raised when a requested workspace grant is not attachable.

    Covers a workspace belonging to a different organization, a workspace that
    does not exist, one that is archived or suspended, and a grant list past
    INVITATION_MAX_GRANTS.

    The cross-organization case is a cross-tenant privilege escalation attempt
    and the whole invitation is rejected rather than the offending grant being
    filtered out — see ARCH-04 §D6.4. Maps to 400.
    """
    pass


class InvitationResendTooSoonError(InvitationError):
    """
    Raised when a resend is requested inside the cooldown window.

    A resend rotates the token (§D6.6), so an unbounded resend endpoint is
    both a mail-volume amplifier and a token churn primitive. Maps to 429.
    """
    pass


# ============================================================================
# Users (account, profile)
# ============================================================================

class UserError(FlowPilotError):
    """Base exception for all account- and profile-related failures."""
    pass


class EmailImmutableError(UserError):
    """
    Raised when a request attempts to change `users.email` outside signup.

    ARCH-05 §B.5 / §A.2.5: email mutability was an absence in this product,
    not a decision, and left open it would have silently broken invitation
    matching (`_assert_actor_matches` compares session email to
    `invitation.email`), `uq_pending_organization_invitation`'s
    `lower(email)` index, password-reset targeting, and
    `OrganizationInvitation.invited_user_id`'s documented status as a stale
    binding that must never authorize. §B.5 closes the surface instead:
    email is immutable for this phase, decided rather than merely unbuilt.

    THIS IS DEFENCE IN DEPTH, NOT THE PRIMARY ENFORCEMENT. The primary
    enforcement is that `UserProfileUpdate` has no `email` field for a
    client to populate — FastAPI/Pydantic reject an unrecognised `email` key
    in the request body before a handler ever runs, so most callers never
    reach code that could raise this. This exception exists for every other
    path to the same data: a service function called directly, a future
    admin tool, a script — anywhere `email` might be assigned outside the
    signup flow that is the sole legitimate writer of that column.

    The message names the actual remedy (contact support; a full,
    verified change flow is scheduled, see §B.5 Option B) rather than
    stating "not allowed" and leaving the reader to guess whether this is
    temporary, a bug, or permanent. Maps to 409 — the request conflicts with
    an immutability rule on the resource, not a validation failure (422) or
    a missing resource (404).
    """
    pass


class ReauthenticationFailedError(UserError):
    """
    Raised when a sensitive action's password confirmation is wrong.

    ARCH-05 §B.2: initiating an ownership transfer requires the outgoing
    owner's CURRENT password, re-entered at that moment, even though the
    request already carries a valid session. The reasoning is the same
    `change_password` already documents for itself (`password_service.py`):
    an access token is a bearer credential that may have been taken, and a
    stolen session should not be enough on its own to hand a tenant to an
    address the attacker controls.

    THIS IS A DIFFERENT EXCEPTION FROM `password_service.IncorrectPasswordError`,
    DELIBERATELY, not an oversight. That class predates the centralized
    `FlowPilotError` hierarchy this module belongs to (it subclasses plain
    `Exception` and is caught with a manual `try/except -> HTTPException` in
    `app/api/v1/auth.py`, rather than through `exception_handlers.py`'s
    registry). Reusing it here would mean importing an exception type out of
    an unrelated service module, and would tie this workflow to
    `change_password`'s error handling being upgraded to the centralized
    pattern before this one could be. A second class with a near-identical
    name is a real cost — noted here so it is not mistaken for the same
    class later — but it is smaller than the coupling the alternative would
    create.

    Maps to 401, not the legacy code's 400. A wrong password IS a failure to
    prove identity for this specific action, which is what 401 signals; 400
    would suggest the request itself was malformed, which it was not.
    """
    pass


# ============================================================================
# Ownership Transfer (ARCH-05 Step 6)
# ============================================================================

class OwnershipTransferError(FlowPilotError):
    """Base exception for all ownership-transfer workflow failures."""
    pass


class PendingTransferExistsError(OwnershipTransferError):
    """
    Raised when initiation is attempted while the organization already has a
    PENDING proposal.

    Backed by `uq_pending_ownership_transfer_per_org` (ARCH-05 Step 3/4) as
    the actual enforcement — this exception is the readable failure surfaced
    when a pre-check finds that row, not a substitute for the constraint.
    The constraint is what makes the invariant true under concurrency; the
    pre-check exists only so two well-behaved sequential requests get a
    clear 409 instead of a raw `IntegrityError`.
    """
    pass


class TransferNotFoundError(OwnershipTransferError):
    """
    Raised when a transfer_id does not resolve within the given organization.

    Deliberately the same failure whether the id is well-formed but wrong,
    or belongs to a different organization entirely — an actor authorized
    for one tenant must not be able to distinguish "no such transfer" from
    "that transfer exists, but not here" by the error they get back. Mirrors
    `get_membership_or_raise`'s own reasoning for the same shape of check.
    """
    pass


class TransferNotPendingError(OwnershipTransferError):
    """
    Raised when accept/decline/cancel is attempted on a transfer that is no
    longer PENDING.

    This is the failure mode of the conditional UPDATE
    (`ownership_transfer_crud.update_transfer_status`) finding no matching
    row: the proposal was already accepted, declined, cancelled, or expired
    — including by a concurrent request that won the race. The message does
    not claim to know WHICH terminal state it landed in, because the
    conditional UPDATE's WHERE clause does not tell you what it did not
    match; a caller that needs to know re-reads the row on the failure path,
    exactly as `claim_invitation`'s callers do.
    """
    pass


class TransferExpiredError(OwnershipTransferError):
    """
    Raised when a PENDING transfer's `expires_at` has passed.

    ARCH-05 §B.8: lazy expiry, no sweeper. This is not merely a read of a
    stale `status` column — it is the SOLE place `OwnershipTransferStatus.EXPIRED`
    is ever written (see the model's own docstring). Whichever service
    function discovers the expiry first writes it, then raises this.

    Maps to 410 Gone, not 409: the proposal existed and is now permanently
    unavailable, which is precisely what 410 signals and 409 does not.
    """
    pass


class TransferTargetMismatchError(OwnershipTransferError):
    """
    Raised when the acting session is not the transfer's target.

    Compares `current_user.id` to `target_membership.user_id` directly — a
    plain identifier comparison, not an email comparison. This deliberately
    does NOT mirror `InvitationEmailMismatchError`'s reasoning
    (`_assert_actor_matches` in `organization_invitation_service.py`),
    because the two situations differ in the one respect that reasoning
    turns on: an invitation's `email` column names an address nobody has
    proved control of yet, so comparing session identity to a stale
    `invited_user_id` would be unsound. `target_membership_id` here is a
    foreign key to an EXISTING, ACTIVE membership of an ALREADY-VERIFIED
    account (A.2.3 requires verification before a transfer can even be
    proposed) — there is no unproven-address problem to route around, and an
    identifier comparison is strictly more precise than a string comparison
    of an immutable (§B.5) email would be.
    """
    pass


class TargetNotVerifiedError(OwnershipTransferError):
    """
    Raised at initiation when the proposed target has no `email_verified_at`.

    ARCH-05 A.2.3: ownership carries `seat_limit` authority today and billing
    liability under Phase F. Handing that to an account that has never
    proved control of its own mailbox is a materially different risk than an
    unverified MEMBER seat — a verified target is what makes it meaningful
    that a person, not an unproven inbox, is the one who will receive and
    can act on the proposal notice.
    """
    pass


class CannotTransferToSelfError(OwnershipTransferError):
    """
    Raised at initiation when the proposed target is the initiator's own
    membership.

    `transfer_ownership` (`organization_member_service.py`) already guards
    the same condition at ACCEPT time, as a safety net that fires regardless
    of how a transfer was created. This exception exists so the same
    nonsensical request is rejected immediately at INITIATION, rather than
    deferred to whoever happens to process acceptance up to seven days
    later.
    """
    pass


class TransferInitiatorMismatchError(OwnershipTransferError):
    """
    Raised when cancellation is attempted by anyone other than the transfer's
    original initiator.

    A deliberately narrower check than `TransferTargetMismatchError`'s: this
    workflow does not let a DIFFERENT current owner cancel a proposal they
    did not make, even to clean up a proposal from someone who has since lost
    ownership themselves. §B.8's 7-day lazy expiry is the intended eventual
    resolution for that specific case, not a broadened cancel permission.
    """
    pass