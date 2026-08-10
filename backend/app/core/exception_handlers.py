"""
Global domain exception translation for FlowPilot AI.

Maps business-level exceptions raised in the service layer to standardized
HTTP responses. Resolution walks the exception's method resolution order, so
a new exception type inherits its base's status code and error code
automatically and only needs an entry here when it requires distinct handling.

Registering a handler for FlowPilotError in main.py is sufficient to cover the
entire taxonomy, because Starlette resolves handlers by walking __mro__.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.exceptions import (
    FlowPilotError,
    InvalidInvitationTokenError,
    InvalidSlugError,
    InvitationAlreadyExistsError,
    InvitationAlreadyMemberError,
    InvitationAlreadyProcessedError,
    InvitationError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationPermissionDeniedError,
    LastOwnerError,
    OrganizationAccessDeniedError,
    OrganizationAlreadyExistsError,
    OrganizationError,
    OrganizationMemberError,
    OrganizationNotFoundError,
    OrganizationPermissionDeniedError,
    ReservedSlugError,
    SlugError,
    SlugUnavailableError,
    TenantSuspendedError,
    WorkspaceAccessDeniedError,
    WorkspaceAlreadyExistsError,
    WorkspaceError,
    WorkspaceMemberError,
    WorkspaceNotFoundError,
    WorkspacePermissionDeniedError,
    InvitationEmailMismatchError,
    SeatLimitExceededError,
    InvitationGrantError,
    InvitationResendTooSoonError,
)

logger = logging.getLogger("app.core.exception_handlers")


class ErrorResponse(BaseModel):
    """
    Standardized Error Contract across the FlowPilot AI platform.
    """
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


#: Default response for any domain exception without an explicit mapping.
_DEFAULT_MAPPING: tuple[int, str] = (400, "BAD_REQUEST")


#: Exception class to (HTTP status, stable error code).
#:
#: Only classes needing behavior distinct from their base appear here.
#: Resolution walks __mro__, so subclasses inherit their base's entry.
#:
#: Note the deliberate 404 on both AccessDenied variants: a 403 would confirm
#: that a tenant exists to an actor who is not a member, which is an
#: enumeration oracle. GitHub applies the same rule to private repositories.
_EXCEPTION_MAPPING: dict[type[Exception], tuple[int, str]] = {
    # --- Organizations -----------------------------------------------------
    OrganizationNotFoundError: (404, "RESOURCE_NOT_FOUND"),
    OrganizationAccessDeniedError: (404, "RESOURCE_NOT_FOUND"),
    OrganizationPermissionDeniedError: (403, "PERMISSION_DENIED"),
    OrganizationAlreadyExistsError: (409, "ORGANIZATION_ALREADY_EXISTS"),
    LastOwnerError: (409, "LAST_OWNER"),
    OrganizationMemberError: (400, "ORGANIZATION_MEMBER_ERROR"),
    OrganizationError: (400, "ORGANIZATION_ERROR"),

    # --- Workspaces --------------------------------------------------------
    WorkspaceNotFoundError: (404, "RESOURCE_NOT_FOUND"),
    WorkspaceAccessDeniedError: (404, "RESOURCE_NOT_FOUND"),
    WorkspacePermissionDeniedError: (403, "PERMISSION_DENIED"),
    WorkspaceAlreadyExistsError: (409, "WORKSPACE_ALREADY_EXISTS"),
    WorkspaceMemberError: (400, "WORKSPACE_MEMBER_ERROR"),
    WorkspaceError: (400, "WORKSPACE_ERROR"),

    # --- Tenant status -----------------------------------------------------
    TenantSuspendedError: (403, "TENANT_SUSPENDED"),

    # --- Slugs -------------------------------------------------------------
    InvalidSlugError: (422, "INVALID_SLUG"),
    ReservedSlugError: (409, "SLUG_RESERVED"),
    SlugUnavailableError: (409, "SLUG_UNAVAILABLE"),
    SlugError: (400, "SLUG_ERROR"),

    # --- Invitations -------------------------------------------------------
    InvitationNotFoundError: (404, "RESOURCE_NOT_FOUND"),
    InvitationPermissionDeniedError: (403, "PERMISSION_DENIED"),
    InvitationEmailMismatchError: (403, "INVITATION_EMAIL_MISMATCH"),
    InvitationExpiredError: (400, "INVITATION_EXPIRED"),
    InvitationAlreadyProcessedError: (409, "INVITATION_ALREADY_PROCESSED"),
    InvitationAlreadyMemberError: (409, "INVITATION_ALREADY_MEMBER"),
    InvitationAlreadyExistsError: (409, "INVITATION_ALREADY_EXISTS"),
    InvalidInvitationTokenError: (400, "INVALID_INVITATION_TOKEN"),
    SeatLimitExceededError: (409, "SEAT_LIMIT_EXCEEDED"),
    InvitationGrantError: (400, "INVITATION_GRANT_INVALID"),
    InvitationResendTooSoonError: (429, "INVITATION_RESEND_TOO_SOON"),
    InvitationError: (400, "INVITATION_ERROR"),

    # --- Root --------------------------------------------------------------
    FlowPilotError: _DEFAULT_MAPPING,
}


def resolve_exception_mapping(exc: Exception) -> tuple[int, str]:
    """
    Resolves an exception to its HTTP status code and stable error code.

    Walks the method resolution order so that subclasses inherit their
    nearest mapped ancestor's behavior.
    """
    for klass in type(exc).__mro__:
        mapping = _EXCEPTION_MAPPING.get(klass)
        if mapping is not None:
            return mapping
    return _DEFAULT_MAPPING


async def domain_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Unified domain exception translator. Maps business-level custom exceptions
    to standardized HTTP response codes and error contracts.
    """
    status_code, code = resolve_exception_mapping(exc)

    logger.debug(
        "Domain exception on %s %s | %s -> %s (%s)",
        request.method,
        request.url.path,
        type(exc).__name__,
        status_code,
        code,
    )

    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            code=code,
            message=str(exc),
            details={},
        ).model_dump(),
    )


async def invitation_error_handler(request: Request, exc: InvitationError) -> JSONResponse:
    """
    Delegates to the standardized domain handler.

    Retained for backwards compatibility with existing registrations.
    """
    return await domain_exception_handler(request, exc)