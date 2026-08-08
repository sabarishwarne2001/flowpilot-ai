"""
Refresh cookie policy for FlowPilot AI.

One module so the attributes are defined once. A cookie set with one Path and
cleared with another is not cleared — the browser keeps the original and the
user stays signed in after asking not to be, which is the kind of bug that
looks like nothing at all in a test that only checks the response body.

ATTRIBUTES, AND WHY EACH ONE
----------------------------
HttpOnly    The refresh token is the long-lived credential. Unreachable from
            JavaScript means an XSS payload can steal a ten-minute access
            token but not a fourteen-day session.

Secure      Everywhere except local development. Browsers treat http://localhost
            as a trustworthy origin and accept Secure cookies over it, but
            Safari does not, so it is switched off in development rather than
            left to differ by browser.

SameSite    Lax (§B.8). app.flowpilot.ai and api.flowpilot.ai share the
            registrable domain flowpilot.ai, so they are same-site and the
            cookie is sent on the refresh XHR. Locally, ports are not part of a
            site, so localhost:3000 and localhost:8000 are same-site too.

            Lax rather than Strict because Strict withholds the cookie on
            top-level navigation *into* the app — a user following an
            invitation or verification link from their mailbox would land
            logged out, having been logged in a moment earlier.

Path        Scoped to the auth routes. The refresh token has no business
            travelling with every request to every endpoint; narrowing the path
            means it is only ever on the wire for the three routes that use it.

Domain      Deliberately absent. Omitting it produces a host-only cookie bound
            to api.flowpilot.ai, which is exactly where the refresh request
            goes. Setting Domain=.flowpilot.ai would instead attach the session
            credential to every present and future subdomain — docs, marketing,
            staging — and a single compromised one anywhere on the domain would
            start receiving it.
"""

from __future__ import annotations

from fastapi import Response

from app.core.config import settings

#: Cookie name. The __Host- prefix is not used because it forbids Path, and
#: scoping the cookie to the auth routes is worth more here than the prefix's
#: guarantee, which SameSite=Lax plus host-only already largely provides.
REFRESH_COOKIE_NAME = "flowpilot_refresh"


def refresh_cookie_path() -> str:
    """The one path the refresh cookie is scoped to."""
    return f"{settings.API_V1_STR}/auth"


def _secure() -> bool:
    return settings.ENVIRONMENT != "development"


def set_refresh_cookie(response: Response, *, token: str) -> None:
    """
    Attaches a rotated or newly issued refresh token.

    max_age matches the session row's expiry so the browser stops sending a
    cookie the server would reject anyway. They are derived from the same
    setting rather than written twice.
    """
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path=refresh_cookie_path(),
        httponly=True,
        secure=_secure(),
        samesite="lax",
    )


def clear_refresh_cookie(response: Response) -> None:
    """
    Removes the refresh cookie.

    Path, Secure and SameSite must match what was set. A browser matches a
    deletion against name, domain and path; a mismatch leaves the original
    cookie in place and the sign-out silently fails.
    """
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=refresh_cookie_path(),
        httponly=True,
        secure=_secure(),
        samesite="lax",
    )