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