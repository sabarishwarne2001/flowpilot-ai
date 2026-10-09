"""
Global domain exception translation for FlowPilot AI.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.public_route_registry import redact_path
from app.core.exceptions import (
    CannotTransferToSelfError,
    EmailImmutableError,
    WeakPasswordError,
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
from app.services.branding.errors import (
    BrandingAssetError,
    BrandingError,
    CertificateProvisioningError,
    CertificateRefusedError,
    CrossTenantAssetError,
    CustomDomainsDisabledError,
    DomainAlreadyClaimedError,
    DomainError,
    DomainLimitExceededError,
    DomainNotFoundError,
    DomainPolicyError,
    DomainVerificationError,
    ResolverUnavailableError,
    SenderDomainError,
)

logger = logging.getLogger("app.core.exception_handlers")


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: Optional[str] = None
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
    WeakPasswordError: (422, "PASSWORD_TOO_WEAK"),
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

    # --- ARCH-25 White-Label, Custom Domains & Branding --------------------
    DomainPolicyError: (422, "DOMAIN_POLICY_ERROR"),
    DomainAlreadyClaimedError: (409, "DOMAIN_ALREADY_CLAIMED"),
    DomainNotFoundError: (404, "DOMAIN_NOT_FOUND"),
    DomainLimitExceededError: (409, "DOMAIN_LIMIT_EXCEEDED"),
    DomainVerificationError: (409, "DOMAIN_VERIFICATION_ERROR"),
    ResolverUnavailableError: (503, "RESOLVER_UNAVAILABLE"),
    CertificateRefusedError: (409, "CERTIFICATE_REFUSED"),
    CertificateProvisioningError: (502, "CERTIFICATE_PROVISIONING_ERROR"),
    CustomDomainsDisabledError: (501, "CUSTOM_DOMAINS_DISABLED"),
    BrandingAssetError: (400, "BRANDING_ASSET_ERROR"),
    CrossTenantAssetError: (404, "CROSS_TENANT_ASSET_ERROR"),
    SenderDomainError: (409, "SENDER_DOMAIN_ERROR"),
    DomainError: (400, "DOMAIN_ERROR"),
    BrandingError: (400, "BRANDING_ERROR"),

    # --- Root --------------------------------------------------------------
    FlowPilotError: _DEFAULT_MAPPING,
}


def resolve_exception_mapping(exc: Exception) -> tuple[int, str]:
    for klass in type(exc).__mro__:
        mapping = _EXCEPTION_MAPPING.get(klass)
        if mapping is not None:
            return mapping
        if hasattr(klass, "status_code"):
            code = getattr(klass, "code", getattr(klass, "error_code", "BAD_REQUEST"))
            return (getattr(klass, "status_code"), str(code))
    return _DEFAULT_MAPPING


def _exception_details(exc: Exception) -> dict:
    """ARCH-30 Tranche 3. Forward structured context a domain error carries.

    Every domain error previously rendered `details={}`, so an error that knew
    which add-on was missing, or which billing state blocked a write, could
    not tell the console. Only a mapping is forwarded; anything else is not a
    details payload and is dropped rather than stringified.
    """
    from collections.abc import Mapping

    raw = getattr(exc, "details", None)
    return dict(raw) if isinstance(raw, Mapping) else {}


async def domain_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    status_code, code = resolve_exception_mapping(exc)

    logger.debug(
        "Domain exception on %s %s | %s -> %s (%s)",
        request.method,
        redact_path(request.url.path),  # ARCH46-S1:log-redaction
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
            detail=str(exc),
            details=_exception_details(exc),
        ).model_dump(),
        headers=headers,
    )


async def invitation_error_handler(request: Request, exc: InvitationError) -> JSONResponse:
    return await domain_exception_handler(request, exc)


#: Keys of a pydantic error entry that carry what the caller submitted.
_VALIDATION_ERROR_ECHO_KEYS = frozenset({"input"})


async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 that never repeats the submitted value back (F-021).

    FastAPI's default handler returns ``exc.errors()`` verbatim, and every entry
    carries ``input``: the exact value the caller sent for the failing field.
    For a field that holds a secret (a BYOK provider key, a password, a reset
    token, an SMTP password, a webhook signing secret) a validation failure
    therefore copied the secret into the response body, and from there into
    browser devtools, proxy logs and error trackers. ``SecretStr`` does not
    help: the error is built from the raw input, not from the model.

    The body keeps the default shape (``{"detail": [{type, loc, msg, ...}]}``),
    so clients that read ``loc`` and ``msg`` are unaffected. Only the echoed
    value is removed, for every field of every endpoint, so a secret field
    added later is covered without anyone remembering to opt in.
    """
    errors = [
        {key: value for key, value in error.items() if key not in _VALIDATION_ERROR_ECHO_KEYS}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": jsonable_encoder(errors)},
    )


# ---------------------------------------------------------------------------
# F-214. Database integrity errors.
#
# Two identical submits at once (a double click, a retried request) both pass a
# route's "does it exist yet?" check, and the second INSERT meets a unique
# constraint. Left unhandled that is a 500 with a traceback, for what is simply
# "somebody did this a moment ago". The constraint is the authority: a unique
# or foreign-key conflict is a 409 the client can refresh after; any other
# integrity refusal (a check or NOT NULL constraint) means the request carried
# something the data rules do not allow, a 422. Both are logged as warnings
# with the constraint's name, so a route that relies on this instead of its
# own validation stays visible.
# ---------------------------------------------------------------------------

_UNIQUE_VIOLATION = "23505"
_FOREIGN_KEY_VIOLATION = "23503"


def _integrity_details(exc: Exception) -> tuple[Optional[str], Optional[str]]:
    orig = getattr(exc, "orig", None)
    pgcode = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
    diag = getattr(orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    return pgcode, constraint


async def integrity_error_handler(request: Request, exc: Exception) -> JSONResponse:
    pgcode, constraint = _integrity_details(exc)
    if pgcode in (_UNIQUE_VIOLATION, _FOREIGN_KEY_VIOLATION):
        status_code, code = status.HTTP_409_CONFLICT, "CONFLICT"
        message = (
            "This was changed by another request at the same time. "
            "Refresh and try again."
        )
    else:
        status_code, code = status.HTTP_422_UNPROCESSABLE_ENTITY, "CONSTRAINT_VIOLATION"
        message = "The request was refused by a data rule. Check the values and try again."
    logger.warning(
        "db.integrity_conflict | %s %s | sqlstate=%s constraint=%s -> %s",
        request.method,
        redact_path(request.url.path),
        pgcode,
        constraint,
        status_code,
    )
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            code=code,
            message=message,
            detail=message,
            details={"constraint": constraint} if constraint else {},
        ).model_dump(),
    )
