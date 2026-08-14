"""
Domain exception taxonomy for FlowPilot AI.

Every business-level failure raised by the service layer inherits from
FlowPilotError.
"""

from __future__ import annotations


# ============================================================================
# Root
# ============================================================================

class FlowPilotError(Exception):
    """Base exception for every FlowPilot AI domain failure."""
    pass


# ============================================================================
# Rate Limiting (ARCH-08 Step 6)
# ============================================================================

class RateLimitExceededError(FlowPilotError):
    """Raised when an operation exceeds its configured rate limit window."""

    def __init__(
        self,
        message: str = "Rate limit exceeded. Please retry shortly.",
        retry_after: int = 60,
        policy: str = "default",
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.policy = policy

    @property
    def response_headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after)}


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
    """
    pass


class OrganizationPermissionDeniedError(OrganizationError):
    """
    Raised when an active member's role is insufficient for the operation.
    """
    pass


class OrganizationMemberError(OrganizationError):
    """Raised when an organization membership constraint is violated."""
    pass


class LastOwnerError(OrganizationMemberError):
    """
    Raised when an operation would leave an organization without an active
    owner.
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
    """
    pass


class WorkspacePermissionDeniedError(WorkspaceError):
    """
    Raised when the actor's effective workspace role is insufficient.
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
    Raised when an organization or workspace is suspended or archived.
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
    Raised when the authenticated actor's email does not match the invited address.
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
    """
    pass


class InvitationGrantError(InvitationError):
    """
    Raised when a requested workspace grant is not attachable.
    """
    pass


class InvitationResendTooSoonError(InvitationError):
    """
    Raised when a resend is requested inside the cooldown window.
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
    """
    pass


class ReauthenticationFailedError(UserError):
    """
    Raised when a sensitive action's password confirmation is wrong.
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
    Raised when initiation is attempted while a transfer is PENDING.
    """
    pass


class TransferNotFoundError(OwnershipTransferError):
    """
    Raised when a transfer_id does not resolve within the organization.
    """
    pass


class TransferNotPendingError(OwnershipTransferError):
    """
    Raised when action is attempted on a transfer that is not PENDING.
    """
    pass


class TransferExpiredError(OwnershipTransferError):
    """
    Raised when a PENDING transfer's `expires_at` has passed.
    """
    pass


class TransferTargetMismatchError(OwnershipTransferError):
    """
    Raised when the acting session is not the transfer's target.
    """
    pass


class TargetNotVerifiedError(OwnershipTransferError):
    """
    Raised when the proposed target has no `email_verified_at`.
    """
    pass


class CannotTransferToSelfError(OwnershipTransferError):
    """
    Raised when the proposed target is the initiator's own membership.
    """
    pass


class TransferInitiatorMismatchError(OwnershipTransferError):
    """
    Raised when cancellation is attempted by anyone other than the initiator.
    """
    pass