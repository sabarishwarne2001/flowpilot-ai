"""
Authentication and session lifecycle router for FlowPilot AI.

    POST   /auth/register        create an account
    POST   /auth/login           credentials -> access token + refresh cookie
    POST   /auth/refresh         refresh cookie -> new access token + rotated cookie
    POST   /auth/logout          end this session
    POST   /auth/logout-all      end every session on every device
    GET    /auth/sessions        list this account's live sessions
    DELETE /auth/sessions/{id}   end one other session
    GET    /auth/me              the authenticated user

THE TWO-CREDENTIAL SPLIT (§B.6)
-------------------------------
    access token   response body, held in memory by the client, ten minutes
    refresh token  HttpOnly cookie scoped to these routes, fourteen days,
                   rotated on every use

Injected script can read the access token and use it for ten minutes. It cannot
read the credential that would let it mint an eleventh. That asymmetry is the
whole point of the split, and it collapses the moment either half is stored the
other's way — an access token in localStorage, or a refresh token in a body.

WHY EVERY MUTATING ROUTE HERE IS POST
-------------------------------------
SameSite=Lax withholds cookies from cross-site subresource requests but sends
them on top-level cross-site *navigation*. A GET /auth/refresh would therefore
fire, cookie attached, from a link on any page. CORS would stop the attacker
reading the response — but the rotation would still happen, and every rotation
invalidates the token the victim is holding. That is a logout oracle for anyone
who can get a victim to follow a link. POST closes it.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api import deps
from app.core.cookies import (
    REFRESH_COOKIE_NAME,
    clear_refresh_cookie,
    set_refresh_cookie,
)
from app.core.security import create_access_token
from app.models.user_session import SessionRevokedReason, UserSession
from app.schemas.auth import (
    SessionResponse,
    TokenResponse,
    UserRegister,
    UserResponse,
)
from app.services import session_service
from app.services.auth_service import authenticate_user, register_new_user

logger = logging.getLogger("app.api.v1.auth")

router = APIRouter(tags=["Authentication"])


# ===========================================================================
# Helpers
# ===========================================================================

def _client_ip(request: Request) -> str | None:
    """
    Best-effort request origin, recorded on the session for incident review.

    X-Forwarded-For is read because the application sits behind a proxy in
    every deployed environment. This value is never used for authorization — a
    header the client controls must not be — only for a human reading a
    session list later.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.client.host[:45] if request.client else None


def _user_agent(request: Request) -> str | None:
    agent = request.headers.get("user-agent")
    return agent[:512] if agent else None


def _refresh_failure(detail: str) -> JSONResponse:
    """
    Builds a 401 that also clears the refresh cookie.

    A JSONResponse rather than `raise HTTPException`, and the difference is not
    stylistic. Headers written to the injected Response object are merged into
    the reply only when the handler *returns*; raising discards that object and
    FastAPI's exception handler builds a fresh reply. Clearing the cookie and
    then raising therefore leaves the browser holding a credential the server
    will never accept again — it retries on a schedule forever, and the user
    sees an app that cannot sign in and cannot sign out.

    Caught by test_reuse_response_clears_the_cookie, which is the only test in
    this file that inspects Set-Cookie on a failure path.
    """
    response = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": detail}
    )
    clear_refresh_cookie(response)
    return response


def _issue(response: Response, *, user_id, issued) -> dict[str, Any]:
    """
    Sets the refresh cookie and builds the access-token body.

    Both credentials come from one session, so the access token's `sid` names
    the session its cookie will rotate. Doing it in one place is what keeps
    login and refresh from drifting apart.
    """
    set_refresh_cookie(response, token=issued.plaintext_token)
    return {
        "access_token": create_access_token(
            subject=user_id, session_id=issued.session_id
        ),
        "token_type": "bearer",
    }


# ===========================================================================
# Registration
# ===========================================================================

@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    user_in: UserRegister,
    db: Session = Depends(deps.get_db),
) -> Any:
    """
    Enrols a new user account.

    No session and no tokens are issued. Registration is not authentication,
    and Step 8 puts email verification between the two.
    """
    try:
        return register_new_user(db, user_in=user_in)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        )


# ===========================================================================
# Login
# ===========================================================================

@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    response: Response,
    db: Session = Depends(deps.get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
) -> Any:
    """
    Exchanges credentials for an access token and a refresh session.

    The response body is unchanged from before Step 7, so a client that has not
    been updated yet keeps working. What is new is the Set-Cookie beside it.
    """
    user = authenticate_user(
        db, email=form_data.username, password=form_data.password
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User account is inactive",
        )

    issued = session_service.create_session(
        db,
        user=user,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
    )
    db.commit()

    logger.info(
        "AUTH_LOGIN | user=%s | session=%s | family=%s",
        user.id,
        issued.session_id,
        issued.family_id,
    )
    return _issue(response, user_id=user.id, issued=issued)


# ===========================================================================
# Refresh
# ===========================================================================

@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(deps.get_db),
    refresh_cookie: str | None = Cookie(
        default=None, alias=REFRESH_COOKIE_NAME
    ),
) -> Any:
    """
    Rotates the refresh session and issues a new access token.

    Unauthenticated by design: the caller's access token has usually just
    expired, which is why they are here. The cookie is the credential.

    Every failure clears the cookie and answers 401. A client left holding a
    cookie the server will never accept would retry on a schedule forever, and
    the correct end state for all of these is identical — sign in again.
    """
    if not refresh_cookie:
        return _refresh_failure("No active session.")

    try:
        issued = session_service.rotate_session(
            db,
            refresh_token=refresh_cookie,
            ip_address=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except session_service.SessionReuseDetectedError:
        # COMMIT, not rollback. rotate_session already revoked the family, and
        # that revocation is the response to the incident. Rolling back here
        # would undo it and leave the replayed token working.
        db.commit()
        return _refresh_failure(
            "Your session was ended because its refresh token was reused. "
            "Please sign in again."
        )
    except session_service.SessionError:
        # Expired or already revoked. rotate_session may have marked the row
        # EXPIRED, which is worth keeping for the same reason.
        db.commit()
        return _refresh_failure(
            "Your session has expired. Please sign in again."
        )

    db.commit()
    return _issue(response, user_id=issued.session.user_id, issued=issued)


# ===========================================================================
# Sign out
# ===========================================================================

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: Session = Depends(deps.get_db),
    refresh_cookie: str | None = Cookie(
        default=None, alias=REFRESH_COOKIE_NAME
    ),
) -> Response:
    """
    Ends this session. Unauthenticated and idempotent, both deliberately.

    A user whose access token has already expired must still be able to sign
    out. A logout that fails because the access credential is stale would leave
    a live fourteen-day refresh session behind — the exact opposite of what was
    asked for. Missing or unknown cookie: clear it and report success.

    Other devices are untouched; that is what logout-all is for.
    """
    if refresh_cookie:
        session = session_service.get_session_by_token(
            db, refresh_token=refresh_cookie
        )
        if session is not None:
            session_service.revoke_session(
                db, session=session, reason=SessionRevokedReason.LOGOUT
            )
            db.commit()
            logger.info(
                "AUTH_LOGOUT | user=%s | session=%s",
                session.user_id,
                session.id,
            )

    clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    response: Response,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
) -> Response:
    """
    Ends every session on every device, immediately.

    Authenticated, unlike /logout, because this acts on sessions the caller is
    not holding. Accepting the cookie alone would let one stolen refresh token
    sign the real owner out everywhere.

    Both halves matter. Revoking the rows stops refresh; the cutoff stamped on
    users.sessions_revoked_at stops the stateless access tokens already in
    flight, which would otherwise stay valid for up to the full access TTL
    after the user asked to be signed out.
    """
    count = session_service.revoke_all_user_sessions(
        db, user=current_user, reason=SessionRevokedReason.LOGOUT_ALL
    )
    db.commit()

    logger.info("AUTH_LOGOUT_ALL | user=%s | sessions=%d", current_user.id, count)
    clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ===========================================================================
# Device management
# ===========================================================================

@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
) -> Any:
    """
    Lists the caller's live sessions — the device list.

    Rotation revokes as it rotates, so this returns one row per device rather
    than every link in every rotation chain.

    No token and no hash is serialized. SessionResponse carries what identifies
    a device to its owner and nothing that could be replayed as a credential.
    """
    return session_service.list_active_sessions(db, user=current_user)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_one_session(
    session_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
) -> Response:
    """
    Ends one session from the device list.

    Ownership is checked against the authenticated user, and a session
    belonging to someone else answers 404 rather than 403. A 403 would confirm
    the identifier names a real session on another account; 404 says only that
    the caller has no such session, which is all they are entitled to know.

    This does NOT stamp sessions_revoked_at. That cutoff is global and would
    sign the user out of every other device too — the opposite of removing one.
    The revoked device's access token therefore survives until its own expiry,
    at most the access TTL. Making that immediate would require a session
    lookup on every request, which §B.6 declined for good reason; ten minutes
    is the accepted cost.
    """
    session = db.get(UserSession, session_id)
    if session is None or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found."
        )

    session_service.revoke_session(
        db, session=session, reason=SessionRevokedReason.LOGOUT
    )
    db.commit()

    logger.info(
        "AUTH_SESSION_REVOKED | user=%s | session=%s",
        current_user.id,
        session_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Introspection
# ===========================================================================

@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user=Depends(deps.get_current_active_user),
) -> Any:
    """
    Returns the currently authenticated user.
    """
    return current_user