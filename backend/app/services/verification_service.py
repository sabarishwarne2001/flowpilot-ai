"""
Email verification for FlowPilot AI.

Registration creates an account; verification proves the address on it belongs
to the person using it. Between those two facts sits every workspace-scoped
route in the product (§B.4).

WHAT VERIFICATION IS ACTUALLY FOR
---------------------------------
Not for confirming a typo. An unverified address is an address that may belong
to someone else — a mistyped one that reaches a real stranger, or a deliberate
one used to squat on a colleague's identity before they sign up. Once that
account holds a workspace seat it holds a stranger's data, and no amount of
later verification undoes what was read.

So the gate is on tenant access, not on login. An unverified user can sign in,
see who they are, and verify. They cannot reach a workspace.

TWO WAYS TO BECOME VERIFIED
---------------------------
1. Consume a verification token that was mailed to the address.

2. Accept a workspace invitation (§B.4, Option 2). The invitation link reached
   the user only through that mailbox, and acceptance additionally requires
   being signed in as an account whose email equals the invited address. Both
   conditions together are the same proof the verification link provides, so
   demanding a second round trip would ask the user to prove twice what they
   have already proved once.

   That equivalence rests entirely on both conditions holding. If invitation
   acceptance ever stops asserting the actor's email against the invitation's,
   this path becomes a way to verify an address you do not control, and it must
   be removed in the same commit.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.auth_token import AuthTokenPurpose
from app.models.user import User
from app.services import auth_token_service

logger = logging.getLogger("app.services.verification")

VERIFY_PURPOSE = AuthTokenPurpose.EMAIL_VERIFICATION


class VerificationError(Exception):
    """Base class for verification failures."""


class AlreadyVerifiedError(VerificationError):
    """The address on this account has already been proved."""


def build_verification_link(token: str) -> str:
    """
    Builds the frontend URL carrying a verification token.

    The token travels in the fragment, never the query string (§B.9). A
    fragment is not sent to any server, so it cannot leak through the Referer
    header to third-party assets on the landing page and cannot be written to a
    proxy or web-server access log.
    """
    return f"{settings.FRONTEND_URL.rstrip('/')}/verify-email#token={token}"


def issue_and_send(
    db: Session,
    *,
    user: User,
    requested_ip: str | None = None,
    requested_user_agent: str | None = None,
) -> bool:
    """
    Issues a verification token and mails it.

    Commits before sending. The token must be durable before the link exists in
    anyone's mailbox — a rollback after a successful send would produce a link
    that looks legitimate and matches nothing, which is indistinguishable from
    tampering to the person holding it.

    Returns True if the message was delivered. Callers in the request path must
    not fail the request on False: a registration that fails because SMTP is
    down converts a mail outage into an inability to sign up (R7). Log it and
    let the user resend.

    Raises:
        AlreadyVerifiedError: The address is already proved.
        auth_token_service.AuthTokenRateLimitError: Ceiling reached.
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
        verify_link=build_verification_link(issued.plaintext_token),
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

    Idempotent in the way that matters: the token is single-use, so a second
    click on the same link fails at consumption rather than re-verifying. The
    UI treats that as success if the account is already verified, because from
    the user's side clicking their link twice should not look like an error.

    Every other outstanding verification token for the account is invalidated.
    Once the address is proved there is nothing left for them to prove, and a
    live token is a live credential.

    Raises:
        auth_token_service.InvalidAuthTokenError
        auth_token_service.ExpiredAuthTokenError
    """
    row = auth_token_service.consume_token(
        db, token=token, purpose=VERIFY_PURPOSE
    )

    user = db.get(User, row.user_id)
    if user is None:
        # The FK is ON DELETE CASCADE, so this means the account is being
        # deleted underneath us. Nothing to verify.
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

    Called from inside accept_workspace_invitation's transaction, after the
    actor's email has been asserted against the invitation's. It does not
    commit — the verification and the membership it was earned by must land
    together or not at all.

    Outstanding verification tokens are invalidated for the same reason as the
    token path: once the address is proved, a live link is a live credential
    with nothing left to authorize.

    Returns True if this call changed anything, so the caller can log the
    transition rather than the no-op.
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