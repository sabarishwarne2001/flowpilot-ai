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
    """The auth routes only: /auth/refresh needs the cookie to rotate it and /auth/logout to end it.

    F-130: it was `/auth/refresh`, so the browser never sent it to /auth/logout and signing out
    ended nothing on the server. The rest of the API still never receives it.
    """
    return f"{settings.API_V1_STR}/auth"


def _legacy_refresh_cookie_path() -> str:
    """Where cookies set before F-130 live. Cleared whenever the cookie is set or cleared."""
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
    _clear_legacy_cookie(response)


def clear_refresh_cookie(response: Response) -> None:
    """Clears the HttpOnly refresh cookie on logout/revocation."""
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=refresh_cookie_path(),
        httponly=True,
        samesite="lax",
        secure=_secure(),
    )
    _clear_legacy_cookie(response)


def _clear_legacy_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=_legacy_refresh_cookie_path(),
        httponly=True,
        samesite="lax",
        secure=_secure(),
    )
