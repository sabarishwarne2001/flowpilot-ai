from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.exceptions import InvitationError


class ErrorResponse(BaseModel):
    """
    Standard error payload returned to clients across FlowPilot AI.

    Example:
        {
            "error": "InvitationNotFoundError",
            "detail": "Invitation not found."
        }
    """

    detail: str
    error: str


async def invitation_error_handler(request: Request, exc: InvitationError) -> JSONResponse:
    """
    Generic handler for InvitationError (and, by inheritance, any of its
    subclasses that aren't given a more specific handler).

    Translates the exception into HTTP 400 with the standard ErrorResponse
    shape. Intentionally does nothing else: no logging, no business logic,
    no database work, no service calls.
    """
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(
            detail=str(exc),
            error=type(exc).__name__,
        ).model_dump(),
    )