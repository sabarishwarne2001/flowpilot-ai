"""
Password reset and change for FlowPilot AI.

Two entry points into one outcome: the stored hash changes, every session dies,
every outstanding reset link is withdrawn, and the account holder is told.

    forgot -> reset      unauthenticated, proved by a token mailed to the
                         address on the account
    change               authenticated, proved by the current password

THE SIDE EFFECTS ARE THE POINT
------------------------------
Changing the hash alone accomplishes very little. A password is changed
overwhelmingly often *because* something is wrong — a shared credential, a
suspected compromise, a laptop left somewhere — and an attacker who already
holds a session does not care what the password becomes. So a completed
password change must also:

  1. Invalidate every outstanding reset token, so a link sitting in a
     compromised mailbox stops working the moment the real owner acts.
  2. Revoke every session row, so no refresh token survives.
  3. Stamp users.sessions_revoked_at, so the stateless access tokens already
     in flight die now rather than at the end of their own TTL (§B.6).

Miss any one and the change is cosmetic. All three happen in one transaction.

VERIFICATION ON RESET (§B.4, extending Option 2)
------------------------------------------------
Completing a reset marks the address verified. The binding here is tighter than
the invitation case: forgot-password looks the user up *by* their own email and
mails that address, so the token cannot reach anywhere other than the account's
own mailbox. There is no second address that could diverge from it.

That tightness depends on the account's email being immutable, which it
currently is — nothing in the codebase changes users.email. If an email-change
feature is ever added it MUST re-set email_verified_at to NULL, or this path
becomes a way to hold a verified flag over an address that was never proved.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_password_hash, verify_password
from app.models.auth_token import AuthTokenPurpose
from app.models.user import User
from app.models.user_session import SessionRevokedReason
from app.services import auth_token_service, session_service

logger = logging.getLogger("app.services.password")

RESET_PURPOSE = AuthTokenPurpose.PASSWORD_RESET


class PasswordError(Exception):
    """Base class for password workflow failures."""


class IncorrectPasswordError(PasswordError):
    """The supplied current password does not match."""


class PasswordUnchangedError(PasswordError):
    """The new password is identical to the existing one."""


def build_reset_link(token: str) -> str:
    """
    Builds the frontend URL carrying a reset token.

    Fragment, never query string (§B.9). A reset link is a password-equivalent
    credential; a fragment is not sent to any server, so it cannot reach a
    proxy log or leak through the Referer header to third-party assets on the
    landing page.
    """
    return f"{settings.FRONTEND_URL.rstrip('/')}/reset-password#token={token}"


# ===========================================================================
# Request a reset
# ===========================================================================

def request_password_reset(
    db: Session,
    *,
    email: str,
    requested_ip: str | None = None,
    requested_user_agent: str | None = None,
) -> None:
    """
    Issues and mails a reset link, if there is an account to mail it to.

    Returns None in every case, including when no account matches, when the
    account is inactive, and when the rate limit has been reached. The caller
    answers 202 unconditionally.

    That silence is the entire security property of this function. Any
    observable difference between "sent" and "no such account" turns the
    endpoint into a membership oracle for arbitrary addresses — and unlike
    /auth/resend-verification, which is authenticated and only ever mails the
    session's own address, this one takes an address from an anonymous caller.

    The rate limit is the easiest of those differences to leak by accident.
    auth_token_service raises on the ceiling; propagating that as 429 would
    answer "this account exists and someone has been asking about it". It is
    caught here and logged instead.
    """
    normalized = (email or "").strip().lower()
    user = db.query(User).filter(User.email == normalized).one_or_none()

    if user is None:
        logger.info("PASSWORD_RESET_REQUESTED | no matching account")
        return

    if not user.is_active:
        # No mail, no token. A deactivated account must not be recoverable by
        # its former holder, and saying so would confirm the address is known.
        logger.info(
            "PASSWORD_RESET_REQUESTED | user=%s | skipped: inactive", user.id
        )
        return

    try:
        issued = auth_token_service.issue_token(
            db,
            user=user,
            purpose=RESET_PURPOSE,
            requested_ip=requested_ip,
            requested_user_agent=requested_user_agent,
        )
    except auth_token_service.AuthTokenRateLimitError:
        logger.warning(
            "PASSWORD_RESET_RATE_LIMITED | user=%s | ip=%s",
            user.id,
            requested_ip or "unknown",
        )
        return

    # Committed before the message is sent. A rollback after a successful send
    # would leave a link that looks legitimate and matches nothing.
    db.commit()

    from app.core.platform_email import (
        PlatformEmailNotConfigured,
        send_platform_email,
    )
    from app.templates.emails.password_reset import render_password_reset

    subject, html_body, text_body = render_password_reset(
        recipient_email=user.email,
        reset_link=build_reset_link(issued.plaintext_token),
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
        logger.error("PASSWORD_RESET_UNCONFIGURED | user=%s | %s", user.id, exc)
        return

    if delivered:
        logger.info("PASSWORD_RESET_SENT | user=%s", user.id)
    else:
        logger.warning(
            "PASSWORD_RESET_SEND_FAILED | user=%s | %s", user.id, detail
        )


# ===========================================================================
# Complete a reset
# ===========================================================================

def reset_password(
    db: Session,
    *,
    token: str,
    new_password: str,
) -> User:
    """
    Consumes a reset token and applies a new password.

    One transaction: consumption, the new hash, verification, token
    invalidation, and session revocation either all land or none do. A partial
    application here is the worst possible outcome — a consumed token with an
    unchanged password locks the user out of their own recovery.

    Raises:
        auth_token_service.InvalidAuthTokenError
        auth_token_service.ExpiredAuthTokenError
        PasswordUnchangedError
    """
    # A SAVEPOINT around the consumption, so a refusal below can undo it
    # precisely.
    #
    # The same-password check needs the account, and the account is reached
    # through the token — so the token has to be consumed before we can tell
    # whether we want to. Raising at that point would leave a spent token and
    # an unchanged password: the user is told to pick a different one, and the
    # link they were told to use is already dead. Locked out of their own
    # recovery by a validation message.
    #
    # A savepoint rather than db.rollback(), which would discard whatever else
    # the caller had pending in this transaction.
    savepoint = db.begin_nested()

    row = auth_token_service.consume_token(
        db, token=token, purpose=RESET_PURPOSE
    )

    user = db.get(User, row.user_id)
    if user is None:
        savepoint.rollback()
        raise auth_token_service.InvalidAuthTokenError("This link is invalid.")

    if verify_password(new_password, user.hashed_password):
        savepoint.rollback()
        raise PasswordUnchangedError(
            "Choose a password you have not used on this account before."
        )

    savepoint.commit()

    user.hashed_password = get_password_hash(new_password)

    # Verification earned by the reset (§B.4, extending Option 2). The token
    # reached the address on this account and nowhere else — forgot-password
    # looks the user up by that address and mails it directly, so unlike the
    # invitation path there is no second address that could diverge from it.
    newly_verified = user.email_verified_at is None
    if newly_verified:
        user.email_verified_at = datetime.now(UTC)

    db.add(user)

    _apply_password_change_side_effects(
        db,
        user=user,
        reason="password reset completed",
        revocation_reason=SessionRevokedReason.PASSWORD_CHANGE,
    )
    db.commit()

    logger.info(
        "PASSWORD_RESET_COMPLETED | user=%s | token=%s | verified_now=%s",
        user.id,
        row.id,
        newly_verified,
    )
    return user


# ===========================================================================
# Change a known password
# ===========================================================================

def change_password(
    db: Session,
    *,
    user: User,
    current_password: str,
    new_password: str,
) -> User:
    """
    Replaces a password the caller already knows.

    The current password is required even though the caller is authenticated.
    An access token is a bearer credential that may have been taken; asking for
    the password is what stops a stolen session from locking the real owner out
    of their own account.

    Sessions are revoked here too, including the caller's own. The endpoint
    issues a fresh session for the device that made the request, so the person
    who just changed their password stays signed in while every other device —
    and every access token anywhere — stops working immediately.

    Raises:
        IncorrectPasswordError
        PasswordUnchangedError
    """
    if not verify_password(current_password, user.hashed_password):
        logger.warning("PASSWORD_CHANGE_REJECTED | user=%s | bad current", user.id)
        raise IncorrectPasswordError("Your current password is incorrect.")

    if verify_password(new_password, user.hashed_password):
        raise PasswordUnchangedError(
            "Your new password must differ from your current one."
        )

    user.hashed_password = get_password_hash(new_password)
    db.add(user)

    _apply_password_change_side_effects(
        db,
        user=user,
        reason="password changed",
        revocation_reason=SessionRevokedReason.PASSWORD_CHANGE,
    )
    db.commit()

    logger.info("PASSWORD_CHANGED | user=%s", user.id)
    return user


# ===========================================================================
# Shared side effects
# ===========================================================================

def _apply_password_change_side_effects(
    db: Session,
    *,
    user: User,
    reason: str,
    revocation_reason: SessionRevokedReason,
) -> None:
    """
    The three things that must accompany every password change.

    Factored out so reset and change cannot drift apart. A future third entry
    point — an administrative reset, an SSO link — must call this too, and
    having one function makes that hard to forget.

    Does not commit. The caller owns the transaction, because these must land
    with the new hash or not at all.
    """
    # 1. Every other outstanding reset link dies. This is what kills a link
    #    delivered to a mailbox the attacker also reads.
    invalidated = auth_token_service.invalidate_outstanding(
        db, user_id=user.id, purpose=RESET_PURPOSE, reason=reason
    )

    # 2 and 3. Session rows revoked AND the global cutoff stamped. The rows
    #    stop refresh; the cutoff stops the stateless access tokens already in
    #    flight, which would otherwise stay valid for up to the full access TTL
    #    after the password protecting them was replaced (§B.6).
    revoked = session_service.revoke_all_user_sessions(
        db, user=user, reason=revocation_reason
    )

    logger.info(
        "PASSWORD_SIDE_EFFECTS | user=%s | reset_tokens_invalidated=%d | "
        "sessions_revoked=%d | cutoff=%s",
        user.id,
        invalidated,
        revoked,
        user.sessions_revoked_at.isoformat() if user.sessions_revoked_at else None,
    )


def send_password_changed_notice(*, user_email: str, changed_at: datetime) -> bool:
    """
    Tells the account holder their password changed.

    Sent on every successful reset and every change, and it cannot be turned
    off. For a user who did not do this, it is the only signal they will get —
    which is why it carries no link that grants anything. It is the one identity
    email that may be read by someone who already controls the session, so it
    is deliberately a dead end: it says what happened and where to go, and gives
    a thief nothing to click.

    Returns False on failure. Never raises to the caller: the password has
    already changed, and a failed notification must not be reported as a failed
    change.
    """
    from app.core.platform_email import (
        PlatformEmailNotConfigured,
        send_platform_email,
    )
    from app.templates.emails.password_changed import render_password_changed

    subject, html_body, text_body = render_password_changed(
        recipient_email=user_email,
        changed_at_str=changed_at.strftime("%Y-%m-%d %H:%M UTC"),
        brand_name=settings.PROJECT_NAME,
        support_email=settings.PLATFORM_SMTP_FROM_EMAIL,
    )

    try:
        delivered, detail = send_platform_email(
            recipient=user_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
    except PlatformEmailNotConfigured as exc:
        logger.error("PASSWORD_CHANGED_NOTICE_UNCONFIGURED | %s", exc)
        return False

    if not delivered:
        logger.warning("PASSWORD_CHANGED_NOTICE_FAILED | %s", detail)
    return delivered