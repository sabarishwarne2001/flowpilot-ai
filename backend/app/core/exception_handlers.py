from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.exceptions import (
    InvitationError,
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceMemberError,
    InvitationNotFoundError,
    InvitationPermissionDeniedError,
)


class ErrorResponse(BaseModel):
    """
    Standardized Error Contract across the FlowPilot AI platform.
    """
    code: str
    message: str
    details: dict = {}


async def domain_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Unified domain exception translator. Maps business-level custom exceptions
    to standardized HTTP response codes and error contracts.
    """
    status_code = 400
    code = "BAD_REQUEST"
    message = str(exc)

    if isinstance(exc, (WorkspaceNotFoundError, InvitationNotFoundError)):
        status_code = 404
        code = "RESOURCE_NOT_FOUND"
    elif isinstance(exc, InvitationPermissionDeniedError):
        status_code = 403
        code = "PERMISSION_DENIED"
    elif type(exc).__name__ == "InvitationExpiredError":
        code = "INVITATION_EXPIRED"
    elif type(exc).__name__ == "InvitationAlreadyProcessedError":
        code = "INVITATION_ALREADY_PROCESSED"
    elif type(exc).__name__ == "InvitationAlreadyMemberError":
        code = "INVITATION_ALREADY_MEMBER"
    elif type(exc).__name__ == "InvalidInvitationTokenError":
        code = "INVALID_INVITATION_TOKEN"
    elif isinstance(exc, WorkspaceMemberError):
        code = "WORKSPACE_MEMBER_ERROR"
    elif isinstance(exc, WorkspaceError):
        code = "WORKSPACE_ERROR"
    elif isinstance(exc, InvitationError):
        code = "INVITATION_ERROR"

    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            code=code,
            message=message,
            details={}
        ).model_dump()
    )


async def invitation_error_handler(request: Request, exc: InvitationError) -> JSONResponse:
    """
    Delegates to the standardized domain handler.
    """
    return await domain_exception_handler(request, exc)