from __future__ import annotations


class WorkspaceError(Exception):
    """Base exception for all workspace-related failures."""
    pass


class WorkspaceNotFoundError(WorkspaceError):
    """Raised when a workspace record is missing or inactive."""
    pass


class WorkspaceAlreadyExistsError(WorkspaceError):
    """Raised when trying to register a duplicate workspace."""
    pass


class WorkspaceMemberError(WorkspaceError):
    """Raised when a membership constraint is violated."""
    pass


class InvitationError(Exception):
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