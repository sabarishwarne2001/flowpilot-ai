"""
HttpOnly refresh cookie helpers for FlowPilot AI (ARCH-03 §B.3).
"""

from __future__ import annotations

import logging
from fastapi import Response

from app.core.config import settings

logger = logging.getLogger("app.core.cookies")

REFRESH_COOKIE_NAME = "flowpilot_refresh"


def refresh_cookie_path() -> str:
    return f"{settings.API_V1_STR}/auth/refresh"


def _secure() -> bool:
    return settings.ENVIRONMENT not in ("development", "test")


def set_refresh_cookie(response: Response, token: str) -> None:
    """Sets the HttpOnly refresh cookie on the response."""
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=_secure(),
        path=refresh_cookie_path(),
    )


def clear_refresh_cookie(response: Response) -> None:
    """Clears the HttpOnly refresh cookie on logout/revocation."""
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=refresh_cookie_path(),
        httponly=True,
        samesite="lax",
        secure=_secure(),
    )
