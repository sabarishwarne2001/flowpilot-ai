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
from datetime import UTC, datetime
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
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
from app.core.config import settings
from app.core.cookies import (
    REFRESH_COOKIE_NAME,
    clear_refresh_cookie,
    set_refresh_cookie,
)
from app.core.security import create_access_token
from app.models.user_session import SessionRevokedReason, UserSession
from app.schemas.auth import (
    ChangePasswordRequest,
    RegistrationAcknowledgement,
    ForgotPasswordRequest,
    PasswordActionResponse,
    ResendVerificationResponse,
    ResetPasswordRequest,
    SessionResponse,
    TokenResponse,
    UserRegister,
    UserResponse,
    VerificationStatusResponse,
    VerifyEmailRequest,
)
from app.services import (
    auth_token_service,
    password_service,
    session_service,
    verification_service,
)
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
    response_model=RegistrationAcknowledgement,
    status_code=status.HTTP_202_ACCEPTED,
)
async def register(
    user_in: UserRegister,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
) -> Any:
    """
    Enrols a new account, or quietly notices the address was already taken.

    ALWAYS 202, with the same body. As of Step 10 this endpoint does not reveal
    whether an address already has an account — pasting a list of addresses
    into a sign-up form is exactly the probe /auth/forgot-password was written
    to defeat, and leaving it open here answered the same question one form
    over.

    What differs is only what lands in the mailbox:

        new address       a verification link
        existing address  a notice that somebody tried to sign up with it

    No session and no tokens are issued in either case. Registration is not
    authentication, and the new account has email_verified_at NULL, which lets
    it sign in and see itself but not reach any workspace (§B.4).

    Both emails go out in background tasks whose failure cannot fail this
    request. A registration that 500s because SMTP is down converts a mail
    outage into an inability to sign up (R7).

    BREAKING CHANGE from the previous 201 + UserResponse. There is no user
    object to return, because in one branch there is no user this caller is
    entitled to know about. The frontend shows a "check your email" screen
    instead of navigating straight to sign-in.
    """
    outcome = register_new_user(db, user_in=user_in)

    if outcome.created and outcome.user is not None:
        db.commit()
        background_tasks.add_task(
            _send_verification_safely,
            db=db,
            user=outcome.user,
            ip_address=_client_ip(request),
            user_agent=_user_agent(request),
        )
    else:
        background_tasks.add_task(
            _send_account_exists_safely, email=outcome.email
        )

    return RegistrationAcknowledgement(
        detail=(
            "Check your email. If we could create an account for that "
            "address, a verification link is on its way."
        )
    )


def _send_account_exists_safely(*, email: str) -> None:
    """
    Tells an existing account holder that someone tried to register as them.

    Swallows everything, like every other background mail task here: an
    exception escaping a BackgroundTask aborts any task queued after it, and
    the response has already gone out.
    """
    from app.core.platform_email import send_platform_email
    from app.templates.emails.account_exists import render_account_exists

    base = settings.FRONTEND_URL.rstrip("/")
    try:
        subject, html_body, text_body = render_account_exists(
            recipient_email=email,
            login_url=f"{base}/login",
            reset_url=f"{base}/forgot-password",
            brand_name=settings.PROJECT_NAME,
        )
        send_platform_email(
            recipient=email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
    except Exception as exc:  # noqa: BLE001 — background, nothing to bubble to
        logger.warning("ACCOUNT_EXISTS_NOTICE_FAILED | %s", exc)


def _send_verification_safely(
    *,
    db: Session,
    user,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    """
    Background wrapper that swallows every failure.

    An exception escaping a BackgroundTask is logged by Starlette and nothing
    else happens, but it also aborts any task queued after it. Catching here
    keeps one unreachable mail server from silently cancelling unrelated work.
    """
    try:
        verification_service.issue_and_send(
            db,
            user=user,
            requested_ip=ip_address,
            requested_user_agent=user_agent,
        )
    except Exception as exc:  # noqa: BLE001 — background, nothing to bubble to
        logger.warning(
            "VERIFY_EMAIL_BACKGROUND_FAILED | user=%s | %s", user.id, exc
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
# Email verification (§B.4)
# ===========================================================================

@router.post("/verify-email", response_model=VerificationStatusResponse)
async def verify_email(
    payload: VerifyEmailRequest,
    db: Session = Depends(deps.get_db),
) -> Any:
    """
    Proves the address on an account.

    Unauthenticated by design. The token is the proof, and requiring a session
    as well would break the ordinary case: the link arrives in a mail client
    and opens in whatever browser is default, often signed out.

    The token is submitted in the body, not read from a query parameter. It
    reaches the frontend in the URL fragment (§B.9), which no server sees, and
    posting it back keeps it out of access logs and Referer headers on the way in
    too.

    A second click on the same link answers 200 with already_verified rather
    than an error. From the user's side clicking their own link twice is not a
    failure, and an error screen there generates support requests about an
    account that is working perfectly.
    """
    try:
        user = verification_service.verify_email(db, token=payload.token)
    except auth_token_service.ExpiredAuthTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This verification link has expired. Sign in and request a "
                "new one."
            ),
        )
    except auth_token_service.InvalidAuthTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This verification link is invalid or has already been used.",
        )

    return VerificationStatusResponse(
        email=user.email,
        email_verified_at=user.email_verified_at,
        already_verified=False,
    )


@router.post(
    "/resend-verification",
    response_model=ResendVerificationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resend_verification(
    request: Request,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
) -> Any:
    """
    Sends a fresh verification link to the caller's own address.

    Authenticated, and it takes no email parameter. That is what keeps it from
    being an account-enumeration oracle: there is no address to probe, because
    the only address it will ever mail is the one on the session. An
    unauthenticated "resend to this address" endpoint answers a different
    question — does this account exist — to anyone who asks.

    Unverified users can reach this because they can sign in; that is the whole
    point of gating tenant access rather than login (§B.4).

    Rate limiting is auth_token_service's, applied per user per purpose. It
    answers 429 rather than pretending to succeed: the caller is authenticated,
    so there is nothing to hide from them, and a fake success leaves them
    waiting for mail that is not coming.
    """
    try:
        delivered = verification_service.issue_and_send(
            db,
            user=current_user,
            requested_ip=_client_ip(request),
            requested_user_agent=_user_agent(request),
        )
    except verification_service.AlreadyVerifiedError:
        return ResendVerificationResponse(
            delivered=False,
            detail="This address is already verified.",
        )
    except auth_token_service.AuthTokenRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        )

    if delivered:
        return ResendVerificationResponse(
            delivered=True,
            detail="Verification email sent. Check your inbox.",
        )

    # 202 with delivered=False, not a 500. The token exists and is valid; only
    # the delivery failed, and the account is not broken (R7).
    logger.warning(
        "VERIFY_EMAIL_RESEND_UNDELIVERED | user=%s", current_user.id
    )
    return ResendVerificationResponse(
        delivered=False,
        detail=(
            "We could not send the email just now. Please try again in a "
            "few minutes."
        ),
    )


# ===========================================================================
# Password reset and change (§B.2, §B.6)
# ===========================================================================

@router.post(
    "/forgot-password",
    response_model=PasswordActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
) -> Any:
    """
    Requests a password reset link.

    ALWAYS 202, with the same body, whatever happened. No account, inactive
    account, rate limit reached, SMTP down — all identical from outside.

    This endpoint takes an arbitrary address from an anonymous caller, so any
    observable difference makes it a membership oracle: paste a list of
    addresses in, learn which ones have accounts here. That is why the rate
    limit does not surface as 429 the way it does on /auth/resend-verification,
    which is authenticated and only ever mails the session's own address.

    The work runs in a background task, which also flattens the timing: the
    response does not wait on a database write or an SMTP connection, so a hit
    and a miss take the same time from the caller's side.
    """
    background_tasks.add_task(
        _request_reset_safely,
        db=db,
        email=payload.email,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
    )

    return PasswordActionResponse(
        detail=(
            "If an account exists for that address, a password reset link is "
            "on its way."
        ),
        sessions_revoked=False,
    )


def _request_reset_safely(
    *,
    db: Session,
    email: str,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    """
    Background wrapper that swallows every failure.

    An exception escaping a BackgroundTask aborts any task queued after it, and
    nothing here is worth surfacing anyway — the response has already gone out
    and must not depend on the outcome.
    """
    try:
        password_service.request_password_reset(
            db,
            email=email,
            requested_ip=ip_address,
            requested_user_agent=user_agent,
        )
    except Exception as exc:  # noqa: BLE001 — background, nothing to bubble to
        logger.warning("PASSWORD_RESET_BACKGROUND_FAILED | %s", exc)


@router.post("/reset-password", response_model=PasswordActionResponse)
async def reset_password(
    payload: ResetPasswordRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
) -> Any:
    """
    Completes a reset and signs every device out.

    No session is issued. Completing a reset does not sign the user in — they
    sign in with the password they just chose, which is one extra step and one
    fewer credential path to secure. Minting a session here would mean an
    unauthenticated endpoint that sets a refresh cookie, reachable by anyone
    holding a link that is sitting in a mailbox.

    The refresh cookie is cleared, because whatever session this browser held
    has just been revoked along with all the others and the cookie would
    otherwise be retried until it expired.

    Completing a reset also marks the address verified (§B.4). The token
    reached the address on this account and nowhere else — forgot-password
    looks the user up by that address (§B.4).
    """
    try:
        user = password_service.reset_password(
            db, token=payload.token, new_password=payload.new_password
        )
    except auth_token_service.ExpiredAuthTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link has expired. Request a new one.",
        )
    except auth_token_service.InvalidAuthTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has already been used.",
        )
    except password_service.PasswordUnchangedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )

    background_tasks.add_task(
        password_service.send_password_changed_notice,
        user_email=user.email,
        changed_at=datetime.now(UTC),
    )
    clear_refresh_cookie(response)

    return PasswordActionResponse(
        detail=(
            "Your password has been reset and every device has been signed "
            "out. Sign in with your new password."
        )
    )


@router.post("/change-password", response_model=TokenResponse)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
) -> Any:
    """
    Replaces a known password and re-establishes this device only.

    Everything is revoked, including the caller's own session, and then a fresh
    session is issued for the device that made the request. So a user who
    changes their password stays where they are while every other device — and
    every access token anywhere — stops working immediately.

    Revoking all and re-issuing, rather than sparing the current session, is
    what makes this useful when the reason for the change is a suspected
    compromise. Sparing the caller would also spare an attacker who happened to
    be the caller.

    This is where Step 7's whole-second revocation comparison earns its keep.
    The cutoff and the new token's iat land in the same wall-clock second;
    comparing an integer iat against a microsecond cutoff would reject the
    token this endpoint just issued, on some fraction of calls, for no reason
    a user could ever act on.

    Deliberately NOT behind the verification gate. An unverified user with a
    working password may change it — verification governs tenant access, not
    account self-management.
    """
    try:
        user = password_service.change_password(
            db,
            user=current_user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except password_service.IncorrectPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    except password_service.PasswordUnchangedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )

    issued = session_service.create_session(
        db,
        user=user,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
    )
    db.commit()

    background_tasks.add_task(
        password_service.send_password_changed_notice,
        user_email=user.email,
        changed_at=datetime.now(UTC),
    )

    logger.info(
        "AUTH_PASSWORD_CHANGED | user=%s | new session=%s",
        user.id,
        issued.session_id,
    )
    return _issue(response, user_id=user.id, issued=issued)


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