"""
Authentication and session lifecycle router for FlowPilot AI.
"""

from __future__ import annotations
from fastapi import Request

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
from app.core.client_ip import client_ip, trusted_client_ip
from app.core.config import settings
from app.core.cookies import (
    REFRESH_COOKIE_NAME,
    clear_refresh_cookie,
    set_refresh_cookie,
)
from app.core.redirects import sanitize_redirect_path
from app.core.security import create_access_token
from app.db.session import SessionLocal
from app.models.user import User
from app.models.user_session import SessionRevokedReason, UserSession
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginResponse,
    MfaLoginRequest,
    PasswordActionResponse,
    RegistrationAcknowledgement,
    ResendVerificationResponse,
    ResetPasswordRequest,
    SessionResponse,
    TokenResponse,
    UserRegister,
    UserResponse,
    VerificationStatusResponse,
    VerifyEmailRequest,
)
from app.schemas.organization_invitation import (
    InvitationSignupRequest,
    InvitationSignupResponse,
)
from app.services import (
    auth_token_service,
    mfa_service,
    password_service,
    session_service,
    verification_service,
)
from app.services.auth_service import authenticate_user, register_new_user
from app.services.login_backoff_service import (
    apply_delay,
    check_login_backoff,
    check_second_step,
    clear_login_backoff,
    clear_second_step,
    record_login_failure,
    record_second_step_failure,
)

logger = logging.getLogger("app.api.v1.auth")

router = APIRouter(tags=["Authentication"])


def _client_ip(request: Request) -> str | None:
    return client_ip(request)


def _user_agent(request: Request) -> str | None:
    agent = request.headers.get("user-agent")
    return agent[:512] if agent else None


def _login_refused() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _refresh_failure(detail: str) -> JSONResponse:
    response = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": detail}
    )
    clear_refresh_cookie(response)
    return response


def _issue(response: Response, *, user_id, issued) -> dict[str, Any]:
    set_refresh_cookie(response, token=issued.plaintext_token)
    return {
        "access_token": create_access_token(
            subject=user_id,
            session_id=issued.session_id,
            authenticated_at=issued.session.authenticated_at,
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
def register(
    user_in: UserRegister,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
) -> Any:
    outcome = register_new_user(db, user_in=user_in)

    if outcome.created and outcome.user is not None:
        db.commit()
        background_tasks.add_task(
            _send_verification_safely,
            user_id=outcome.user.id,
            ip_address=_client_ip(request),
            user_agent=_user_agent(request),
            redirect=sanitize_redirect_path(user_in.redirect),
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


@router.post(
    "/register/invitation",
    response_model=InvitationSignupResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_from_invitation(
    payload: InvitationSignupRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
) -> Any:
    """F-222. Sign up from an invitation: the account, the acceptance and a session, in one step.

    The address is the invitation's (the request cannot name one) and the token,
    emailed to it, proves the caller controls it, so the account starts verified
    and no verification round trip stands between the invitee and the workspace.
    An address that already has an account answers 409 INVITATION_ACCOUNT_EXISTS:
    its owner signs in and accepts. A plain /auth/register is unchanged.
    """
    from app.api.v1.organization_invitations import queue_accepted_mail, queue_seat_blocked_mail
    from app.core.exceptions import SeatLimitExceededError
    from app.services import organization_invitation_service

    try:
        user, accepted = organization_invitation_service.register_and_accept(
            db, token=payload.token, password=payload.password, request=request
        )
    except SeatLimitExceededError:
        queue_seat_blocked_mail(db, token=payload.token)
        raise
    queue_accepted_mail(background_tasks, accepted)
    session = _open_session(db, request, response, user=user, ip=_client_ip(request) or "unknown")
    return InvitationSignupResponse(
        **session,
        organization_slug=accepted.organization_slug,
        workspace_slug=accepted.first_workspace_slug,
    )


def _send_account_exists_safely(*, email: str) -> None:
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
    except Exception as exc:
        logger.warning("ACCOUNT_EXISTS_NOTICE_FAILED | %s", exc)


def _send_verification_safely(
    *,
    user_id: uuid.UUID,
    ip_address: str | None,
    user_agent: str | None,
    redirect: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is not None:
            verification_service.issue_and_send(
                db,
                user=user,
                requested_ip=ip_address,
                requested_user_agent=user_agent,
                redirect=redirect,
            )
            db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning(
            "VERIFY_EMAIL_BACKGROUND_FAILED | user=%s | %s", user_id, exc
        )
    finally:
        db.close()


# ===========================================================================
# Login, Refresh, Logout, Devices, Verify, Password
# ===========================================================================

# F-048: limited once, by the global middleware (the public-route registry
# maps /auth/login to POLICY_LOGIN_IP). A second RateLimiter here drew from the
# same counter, so every attempt cost two and the allowance was halved.
@router.post(
    "/login",
    response_model=LoginResponse,
    response_model_exclude_none=True,
)
def login(
    request: Request,
    response: Response,
    db: Session = Depends(deps.get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
) -> Any:
    ip = _client_ip(request) or "unknown"
    email = form_data.username.strip().lower()

    backoff = check_login_backoff(ip, email)
    apply_delay(backoff.delay_ms)

    if backoff.is_backed_off:
        logger.info(
            "AUTH_LOGIN_REFUSED | reason=pair_backoff | ip=%s | retry_after=%s",
            ip,
            backoff.retry_after_seconds,
        )
        record_login_failure(ip, email)
        raise _login_refused()

    user = authenticate_user(db, email=email, password=form_data.password)

    if not user or not user.is_active:
        record_login_failure(ip, email)
        logger.info(
            "AUTH_LOGIN_REFUSED | reason=%s | ip=%s",
            "inactive_account" if user else "bad_credentials",
            ip,
        )
        raise _login_refused()

    if mfa_service.is_enabled(db, user.id):
        # N-017: the password was right, but it is only half. No session yet;
        # the backoff is cleared once the second step succeeds.
        logger.info("AUTH_LOGIN_MFA_CHALLENGE | user=%s | ip=%s", user.id, ip)
        return {
            "mfa_required": True,
            "mfa_token": mfa_service.issue_challenge(user),
            "token_type": "mfa",
        }

    clear_login_backoff(ip, email)
    return _open_session(db, request, response, user=user, ip=ip)


def _open_session(db: Session, request: Request, response: Response, *, user: User, ip: str) -> dict[str, Any]:
    issued = session_service.create_session(
        db,
        user=user,
        ip_address=ip,
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


def _second_step_refused() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "MFA_CODE_INVALID",
            "message": "That code is not right, or the sign-in took too long. Check the code and try again.",
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


# N-017: the second sign-in step. Public (the caller has no session yet); limited by
# the same POLICY_LOGIN_IP as /auth/login, by the (ip, email) backoff ladder, and by
# a per-account cap on wrong codes (login_backoff_service.SECOND_FACTOR_MAX_FAILURES).
@router.post("/login/mfa", response_model=TokenResponse)
def login_second_factor(
    payload: MfaLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(deps.get_db),
) -> Any:
    ip = _client_ip(request) or "unknown"
    user_id = mfa_service.read_challenge(payload.mfa_token)
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.is_active or not mfa_service.is_enabled(db, user.id):
        raise _second_step_refused()

    email = user.email.strip().lower()
    backoff = check_second_step(ip, email)
    apply_delay(backoff.delay_ms)
    if backoff.is_backed_off:
        logger.info("AUTH_LOGIN_MFA_REFUSED | reason=locked | user=%s | ip=%s", user.id, ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "MFA_LOCKED",
                "message": "Too many wrong codes. Wait 15 minutes, then sign in again.",
            },
        )

    if not mfa_service.verify_second_factor(db, user=user, code=payload.code):
        db.commit()
        record_second_step_failure(ip, email)
        logger.info("AUTH_LOGIN_MFA_REFUSED | reason=bad_code | user=%s | ip=%s", user.id, ip)
        raise _second_step_refused()

    clear_second_step(ip, email)
    return _open_session(db, request, response, user=user, ip=ip)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(deps.get_db),
    refresh_cookie: str | None = Cookie(
        default=None, alias=REFRESH_COOKIE_NAME
    ),
) -> Any:
    if not refresh_cookie:
        return _refresh_failure("No active session.")

    try:
        issued = session_service.rotate_session(
            db,
            refresh_token=refresh_cookie,
            ip_address=_client_ip(request),
            user_agent=_user_agent(request),
            # ARCH-19 §3.4 — the strict resolution, which is None
            # when the ingress chain cannot be trusted. A pinned
            # session then fails closed; an unpinned one is
            # unaffected.
            trusted_ip=trusted_client_ip(request),
        )
    except session_service.SessionReuseDetectedError:
        db.commit()
        return _refresh_failure(
            "Your session was ended because its refresh token was reused. "
            "Please sign in again."
        )
    except session_service.SessionError:
        db.commit()
        return _refresh_failure(
            "Your session has expired. Please sign in again."
        )

    db.commit()
    return _issue(response, user_id=issued.session.user_id, issued=issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    db: Session = Depends(deps.get_db),
    refresh_cookie: str | None = Cookie(
        default=None, alias=REFRESH_COOKIE_NAME
    ),
) -> Response:
    if refresh_cookie:
        session = session_service.get_session_by_token(
            db, refresh_token=refresh_cookie
        )
        if session is not None:
            # The whole sign-in, not the one row: the cookie may be a rotated one (F-126).
            session_service.end_sign_in(
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
def logout_all(
    response: Response,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
) -> Response:
    count = session_service.revoke_all_user_sessions(
        db, user=current_user, reason=SessionRevokedReason.LOGOUT_ALL
    )
    db.commit()

    logger.info("AUTH_LOGOUT_ALL | user=%s | sessions=%d", current_user.id, count)
    clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/sessions", response_model=list[SessionResponse])
def list_sessions(
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
) -> Any:
    return session_service.list_active_sessions(db, user=current_user)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_one_session(
    session_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
) -> Response:
    session = db.get(UserSession, session_id)
    if session is None or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found."
        )

    # The device's whole sign-in, so its older access tokens stop too (F-126).
    session_service.end_sign_in(
        db, session=session, reason=SessionRevokedReason.LOGOUT
    )
    db.commit()

    logger.info(
        "AUTH_SESSION_REVOKED | user=%s | session=%s",
        current_user.id,
        session_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/verify-email", response_model=VerificationStatusResponse)
def verify_email(
    payload: VerifyEmailRequest,
    db: Session = Depends(deps.get_db),
) -> Any:
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
def resend_verification(
    request: Request,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
) -> Any:
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


@router.post(
    "/forgot-password",
    response_model=PasswordActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
) -> Any:
    background_tasks.add_task(
        _request_reset_safely,
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
    email: str,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    db = SessionLocal()
    try:
        password_service.request_password_reset(
            db,
            email=email,
            requested_ip=ip_address,
            requested_user_agent=user_agent,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("PASSWORD_RESET_BACKGROUND_FAILED | %s", exc)
    finally:
        db.close()


@router.post("/reset-password", response_model=PasswordActionResponse)
def reset_password(
    payload: ResetPasswordRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
) -> Any:
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
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
) -> Any:
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


@router.get("/me", response_model=UserResponse)
def get_me(
    current_user=Depends(deps.get_current_active_user),
) -> Any:
    return current_user
