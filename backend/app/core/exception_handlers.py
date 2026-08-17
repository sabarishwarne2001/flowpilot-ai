"""
Global domain exception translation for FlowPilot AI.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.exceptions import (
    CannotTransferToSelfError,
    EmailImmutableError,
    FlowPilotError,
    InvalidInvitationTokenError,
    InvalidSlugError,
    InvitationAlreadyExistsError,
    InvitationAlreadyMemberError,
    InvitationAlreadyProcessedError,
    InvitationEmailMismatchError,
    InvitationError,
    InvitationExpiredError,
    InvitationGrantError,
    InvitationNotFoundError,
    InvitationPermissionDeniedError,
    InvitationResendTooSoonError,
    LastOwnerError,
    OrganizationAccessDeniedError,
    OrganizationAlreadyExistsError,
    OrganizationError,
    OrganizationMemberError,
    OrganizationNotFoundError,
    OrganizationPermissionDeniedError,
    OwnershipTransferError,
    PendingTransferExistsError,
    RateLimitExceededError,
    ReauthenticationFailedError,
    ReservedSlugError,
    SeatLimitExceededError,
    SlugError,
    SlugUnavailableError,
    SpendControlError,
    SpendLimitExceededError,
    SpendLimitMisconfiguredError,
    TargetNotVerifiedError,
    TenantSuspendedError,
    TransferExpiredError,
    TransferInitiatorMismatchError,
    TransferNotFoundError,
    TransferNotPendingError,
    TransferTargetMismatchError,
    UserError,
    WorkspaceAccessDeniedError,
    WorkspaceAlreadyExistsError,
    WorkspaceError,
    WorkspaceMemberError,
    WorkspaceNotFoundError,
    WorkspacePermissionDeniedError,
)

logger = logging.getLogger("app.core.exception_handlers")


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


_DEFAULT_MAPPING: tuple[int, str] = (400, "BAD_REQUEST")


_EXCEPTION_MAPPING: dict[type[Exception], tuple[int, str]] = {
    # --- Rate Limiting -----------------------------------------------------
    RateLimitExceededError: (status.HTTP_429_TOO_MANY_REQUESTS, "RATE_LIMIT_EXCEEDED"),

    # --- Spend Controls (ARCH-10 Step 3) -----------------------------------
    SpendLimitExceededError: (status.HTTP_402_PAYMENT_REQUIRED, "SPEND_LIMIT_EXCEEDED"),
    SpendLimitMisconfiguredError: (400, "SPEND_LIMIT_MISCONFIGURED"),
    SpendControlError: (400, "SPEND_CONTROL_ERROR"),

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

    # --- Users ---------------------------------------------------------------
    EmailImmutableError: (409, "EMAIL_IMMUTABLE"),
    ReauthenticationFailedError: (401, "REAUTHENTICATION_FAILED"),
    UserError: (400, "USER_ERROR"),

    # --- Ownership Transfer (ARCH-05 Step 6) --------------------------------
    PendingTransferExistsError: (409, "PENDING_TRANSFER_EXISTS"),
    TransferNotFoundError: (404, "TRANSFER_NOT_FOUND"),
    TransferNotPendingError: (409, "TRANSFER_NOT_PENDING"),
    TransferExpiredError: (410, "TRANSFER_EXPIRED"),
    TransferTargetMismatchError: (403, "TRANSFER_TARGET_MISMATCH"),
    TransferInitiatorMismatchError: (403, "TRANSFER_INITIATOR_MISMATCH"),
    TargetNotVerifiedError: (409, "TARGET_NOT_VERIFIED"),
    CannotTransferToSelfError: (400, "CANNOT_TRANSFER_TO_SELF"),
    OwnershipTransferError: (400, "OWNERSHIP_TRANSFER_ERROR"),

    # --- Root --------------------------------------------------------------
    FlowPilotError: _DEFAULT_MAPPING,
}


def resolve_exception_mapping(exc: Exception) -> tuple[int, str]:
    for klass in type(exc).__mro__:
        mapping = _EXCEPTION_MAPPING.get(klass)
        if mapping is not None:
            return mapping
    return _DEFAULT_MAPPING


async def domain_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    status_code, code = resolve_exception_mapping(exc)

    logger.debug(
        "Domain exception on %s %s | %s -> %s (%s)",
        request.method,
        request.url.path,
        type(exc).__name__,
        status_code,
        code,
    )

    headers = getattr(exc, "response_headers", None)

    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            code=code,
            message=str(exc),
            details={},
        ).model_dump(),
        headers=headers,
    )


async def invitation_error_handler(request: Request, exc: InvitationError) -> JSONResponse:
    return await domain_exception_handler(request, exc)