"""
Email verification for FlowPilot AI.

Registration creates an account; verification proves the address on it belongs
to the person using it. Between those two facts sits every workspace-scoped
route in the product (§B.4).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.redirects import sanitize_redirect_path
from app.models.auth_token import AuthTokenPurpose
from app.models.user import User
from app.services import auth_token_service

logger = logging.getLogger("app.services.verification")

VERIFY_PURPOSE = AuthTokenPurpose.EMAIL_VERIFICATION


class VerificationError(Exception):
    """Base class for verification failures."""


class AlreadyVerifiedError(VerificationError):
    """The address on this account has already been proved."""


def build_verification_link(token: str, redirect: str | None = None) -> str:
    """
    Builds the frontend URL carrying a verification token.

    The token travels in the fragment, never the query string (§B.9).

    ARCH-06 Step 9 (§B.8, Option A): an optional validated destination rides
    alongside the token, IN THE SAME FRAGMENT.

        /verify-email#token=<token>&redirect=%2Facme%2Fengineering
    """
    base = f"{settings.FRONTEND_URL.rstrip('/')}/verify-email#token={token}"

    safe_redirect = sanitize_redirect_path(redirect)
    if safe_redirect is None:
        if redirect:
            logger.warning(
                "VERIFY_LINK_REDIRECT_REJECTED | dropped unsafe redirect value"
            )
        return base

    return f"{base}&redirect={quote(safe_redirect, safe='')}"


def issue_and_send(
    db: Session,
    *,
    user: User,
    requested_ip: str | None = None,
    requested_user_agent: str | None = None,
    redirect: str | None = None,
) -> bool:
    """
    Issues a verification token and mails it.
    """
    if user.email_verified_at is not None:
        raise AlreadyVerifiedError("This address is already verified.")

    issued = auth_token_service.issue_token(
        db,
        user=user,
        purpose=VERIFY_PURPOSE,
        requested_ip=requested_ip,
        requested_user_agent=requested_user_agent,
    )
    db.commit()

    from app.core.platform_email import (
        PlatformEmailNotConfigured,
        send_platform_email,
    )
    from app.templates.emails.verify_email import render_verify_email

    subject, html_body, text_body = render_verify_email(
        recipient_email=user.email,
        verify_link=build_verification_link(issued.plaintext_token, redirect),
        expiry_str=issued.auth_token.expires_at.strftime("%Y-%m-%d %H:%M UTC"),
        brand_name=settings.PROJECT_NAME,
    )

    try:
        delivered, detail = send_platform_email(
            recipient=user.email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
    except PlatformEmailNotConfigured as exc:
        logger.error("VERIFY_EMAIL_UNCONFIGURED | user=%s | %s", user.id, exc)
        return False

    if not delivered:
        logger.warning(
            "VERIFY_EMAIL_SEND_FAILED | user=%s | %s", user.id, detail
        )
    return delivered


def verify_email(db: Session, *, token: str) -> User:
    """
    Consumes a verification token and marks the address proved.
    """
    row = auth_token_service.consume_token(
        db, token=token, purpose=VERIFY_PURPOSE
    )

    user = db.get(User, row.user_id)
    if user is None:
        raise auth_token_service.InvalidAuthTokenError("This link is invalid.")

    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
        db.add(user)

    auth_token_service.invalidate_outstanding(
        db,
        user_id=user.id,
        purpose=VERIFY_PURPOSE,
        reason="email verified",
    )
    db.commit()

    logger.info(
        "EMAIL_VERIFIED | user=%s | via=token | token=%s", user.id, row.id
    )
    return user


def mark_verified_via_invitation(
    db: Session,
    *,
    user: User,
    invitation_id,
) -> bool:
    """
    Records verification earned by accepting an invitation (§B.4, Option 2).
    """
    if user.email_verified_at is not None:
        return False

    user.email_verified_at = datetime.now(UTC)
    db.add(user)

    auth_token_service.invalidate_outstanding(
        db,
        user_id=user.id,
        purpose=VERIFY_PURPOSE,
        reason=f"verified by accepting invitation {invitation_id}",
    )

    logger.info(
        "EMAIL_VERIFIED | user=%s | via=invitation | invitation=%s",
        user.id,
        invitation_id,
    )
    return True