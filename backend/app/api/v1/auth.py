"""
Authentication and session lifecycle router for FlowPilot AI.
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
from app.api.rate_limit_deps import RateLimiter
from app.core.client_ip import client_ip, trusted_client_ip
from app.core.config import settings
from app.core.cookies import (
    REFRESH_COOKIE_NAME,
    clear_refresh_cookie,
    set_refresh_cookie,
)
from app.core.rate_limit.policy import POLICY_LOGIN_IP
from app.core.redirects import sanitize_redirect_path
from app.core.security import create_access_token
from app.db.session import SessionLocal
from app.models.user import User
from app.models.user_session import SessionRevokedReason, UserSession
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
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
from app.services import (
    auth_token_service,
    password_service,
    session_service,
    verification_service,
)
from app.services.auth_service import authenticate_user, register_new_user
from app.services.login_backoff_service import (
    apply_delay,
    check_login_backoff,
    clear_login_backoff,
    record_login_failure,
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
async def register(
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

@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(RateLimiter(POLICY_LOGIN_IP))],
)
async def login(
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

    clear_login_backoff(ip, email)

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


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
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
async def logout(
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
    count = session_service.revoke_all_user_sessions(
        db, user=current_user, reason=SessionRevokedReason.LOGOUT_ALL
    )
    db.commit()

    logger.info("AUTH_LOGOUT_ALL | user=%s | sessions=%d", current_user.id, count)
    clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
) -> Any:
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


@router.post("/verify-email", response_model=VerificationStatusResponse)
async def verify_email(
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
async def resend_verification(
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
async def forgot_password(
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
async def reset_password(
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
async def change_password(
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
async def get_me(
    current_user=Depends(deps.get_current_active_user),
) -> Any:
    return current_user