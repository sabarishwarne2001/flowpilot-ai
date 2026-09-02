"""
Single-use identity tokens for FlowPilot AI.

Email verification and password reset. Both are the same lifecycle — issue a
high-entropy secret, store only its hash, consume exactly once, expire whether
or not it is used — which is why they share one table and one module. The
per-purpose differences that do exist (TTL, what consumption means, what else
gets invalidated) live here rather than in the model.

The plaintext is never stored. It exists in the link in the recipient's
mailbox and in the request body when they submit it.

CONSUMPTION IS A CONDITIONAL UPDATE, NOT A READ THEN A WRITE
------------------------------------------------------------
    UPDATE auth_tokens SET consumed_at = now()
     WHERE token_hash = :hash
       AND purpose = :purpose
       AND consumed_at IS NULL
       AND invalidated_at IS NULL
       AND expires_at > now()
    RETURNING id

A SELECT followed by an UPDATE has a window in which two concurrent requests
both observe an unconsumed row and both proceed — a double-click on a reset
link is enough to hit it. The WHERE clause closes it: the second UPDATE matches
zero rows and its caller sees the token as already spent. There is no lock to
take and no retry to write; the database decides.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.tokens import generate_secure_token, hash_token
from app.models.auth_token import AuthToken, AuthTokenPurpose
from app.models.user import User

logger = logging.getLogger("app.services.auth_token")


# ===========================================================================
# Errors
# ===========================================================================

class AuthTokenError(Exception):
    """Base class for identity-token failures."""


class InvalidAuthTokenError(AuthTokenError):
    """No token matches, or it was already consumed or invalidated."""


class ExpiredAuthTokenError(AuthTokenError):
    """The token matched but is past its expiry."""


class AuthTokenRateLimitError(AuthTokenError):
    """Too many tokens issued for this user and purpose in the window."""


# ===========================================================================
# Carrier
# ===========================================================================

@dataclass(frozen=True)
class IssuedAuthToken:
    """
    A token row together with the plaintext that addresses it.

    Same contract as IssuedInvitation and IssuedSession: the plaintext is
    returned, never persisted, and the caller must build the link before it
    goes out of scope.
    """

    auth_token: AuthToken
    plaintext_token: str


# ===========================================================================
# Purpose policy
# ===========================================================================

def ttl_for(purpose: AuthTokenPurpose) -> timedelta:
    """
    The lifetime of a token issued for a given purpose.

    Reset is an hour, verification a day. A reset link is a
    password-equivalent credential sitting in a mailbox, so its exposure window
    should be as short as a distracted person can still act within. A
    verification link grants nothing by itself, so a day costs little and saves
    a resend for anyone who reads their email the next morning.
    """
    if purpose is AuthTokenPurpose.PASSWORD_RESET:
        return timedelta(minutes=settings.PASSWORD_RESET_TTL_MINUTES)
    return timedelta(hours=settings.EMAIL_VERIFICATION_TTL_HOURS)


# ===========================================================================
# Issuance
# ===========================================================================

def recent_issue_count(
    db: Session,
    *,
    user_id: uuid.UUID,
    purpose: AuthTokenPurpose,
    window: timedelta | None = None,
) -> int:
    """
    Counts tokens issued to this user for this purpose inside the window.

    Served by ix_auth_tokens_user_purpose_consumed, whose leading two columns
    are exactly this predicate.
    """
    window = window or timedelta(minutes=settings.IDENTITY_TOKEN_WINDOW_MINUTES)
    since = datetime.now(UTC) - window

    return db.execute(
        select(func.count())
        .select_from(AuthToken)
        .where(
            AuthToken.user_id == user_id,
            AuthToken.purpose == purpose,
            AuthToken.created_at >= since,
        )
    ).scalar_one()


def issue_token(
    db: Session,
    *,
    user: User,
    purpose: AuthTokenPurpose,
    requested_ip: str | None = None,
    requested_user_agent: str | None = None,
    enforce_rate_limit: bool = True,
) -> IssuedAuthToken:
    """
    Issues a single-use token.

    Participates in the caller's transaction and does not commit, so a token
    is never persisted for an email that the same request then fails to send.

    Previously issued tokens are deliberately left alone. Invalidating them
    here would mean a user who requests a second reset because the first email
    was slow finds the first link dead when it finally arrives — and both links
    are equally legitimate. Outstanding tokens are cleared on *success*
    instead, by invalidate_outstanding.

    Args:
        user: Recipient.
        purpose: What consuming the token will authorize.
        requested_ip: Origin of the request, for incident review.
        requested_user_agent: Client string, for incident review.
        enforce_rate_limit: Set False for administrative reissues.

    Raises:
        AuthTokenRateLimitError: Ceiling reached for this user and purpose.
    """
    if enforce_rate_limit:
        issued_recently = recent_issue_count(db, user_id=user.id, purpose=purpose)
        if issued_recently >= settings.IDENTITY_TOKEN_MAX_PER_WINDOW:
            logger.warning(
                "AUTH_TOKEN_RATE_LIMITED | user=%s | purpose=%s | %d in %dm",
                user.id,
                purpose.value,
                issued_recently,
                settings.IDENTITY_TOKEN_WINDOW_MINUTES,
            )
            raise AuthTokenRateLimitError(
                "Too many requests. Please wait before trying again."
            )

    plaintext = generate_secure_token()

    token = AuthToken(
        user_id=user.id,
        purpose=purpose,
        token_hash=hash_token(plaintext),
        expires_at=datetime.now(UTC) + ttl_for(purpose),
        requested_ip=requested_ip,
        requested_user_agent=requested_user_agent,
    )
    db.add(token)
    db.flush()

    # Id and purpose only. The plaintext and its hash are both derived from a
    # live secret, and an application log is not a secret store (R4).
    logger.info(
        "AUTH_TOKEN_ISSUED | user=%s | purpose=%s | token=%s | expires=%s",
        user.id,
        purpose.value,
        token.id,
        token.expires_at.isoformat(),
    )
    return IssuedAuthToken(auth_token=token, plaintext_token=plaintext)


# ===========================================================================
# Consumption
# ===========================================================================

def consume_token(
    db: Session,
    *,
    token: str,
    purpose: AuthTokenPurpose,
) -> AuthToken:
    """
    Spends a token, atomically, and returns the row.

    The purpose is part of the WHERE clause, not checked afterwards. Both
    purposes are 256-bit secrets in one table, so without it a verification
    token would satisfy a password reset.

    Participates in the caller's transaction. If the caller then fails — the
    password will not hash, the commit is rejected — the consumption rolls back
    with it and the link still works.

    Raises:
        InvalidAuthTokenError: No match, already consumed, or invalidated.
        ExpiredAuthTokenError: Matched but past expiry.
    """
    now = datetime.now(UTC)
    token_hash = hash_token(token)

    consumed_id = db.execute(
        update(AuthToken)
        .where(
            AuthToken.token_hash == token_hash,
            AuthToken.purpose == purpose,
            AuthToken.consumed_at.is_(None),
            AuthToken.invalidated_at.is_(None),
            AuthToken.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(AuthToken.id)
    ).scalar_one_or_none()

    if consumed_id is None:
        raise _classify_consumption_failure(
            db, token_hash=token_hash, purpose=purpose, now=now
        )

    db.flush()

    # refresh, not just get. The UPDATE ran as SQL, so an instance already in
    # the identity map still reports consumed_at as None — and a caller that
    # checks it would conclude the token was never spent. Found by
    # test_consume_marks_the_row_and_returns_it.
    row = db.get(AuthToken, consumed_id)
    assert row is not None  # just updated inside this transaction
    db.refresh(row)

    logger.info(
        "AUTH_TOKEN_CONSUMED | user=%s | purpose=%s | token=%s",
        row.user_id,
        purpose.value,
        row.id,
    )
    return row


def _classify_consumption_failure(
    db: Session,
    *,
    token_hash: str,
    purpose: AuthTokenPurpose,
    now: datetime,
) -> AuthTokenError:
    """
    Works out why the conditional UPDATE matched nothing.

    A second query, run only on the failure path, purely so the API can say
    "this link has expired, request a new one" instead of "invalid link" —
    which sends a user with a perfectly ordinary expired link off to support.

    Distinguishing the cases is safe here in a way it would not be for a
    password. The token is 256 bits of randomness: an attacker cannot produce a
    value that lands in either bucket, so being told which bucket a value fell
    into reveals nothing they could act on. The one thing never distinguished
    is whether the *user* exists, which is decided at issuance, not here.
    """
    row = db.execute(
        select(AuthToken).where(AuthToken.token_hash == token_hash)
    ).scalar_one_or_none()

    if row is None:
        logger.info("AUTH_TOKEN_REJECTED | purpose=%s | reason=no_match", purpose.value)
        return InvalidAuthTokenError("This link is invalid.")

    if row.purpose is not purpose:
        # A real signal. The only way to hold a valid token of the wrong
        # purpose is to have been sent one, so this is either a bug in link
        # construction or a deliberate attempt to cross the two flows.
        logger.warning(
            "AUTH_TOKEN_PURPOSE_MISMATCH | token=%s | user=%s | is=%s | tried=%s",
            row.id,
            row.user_id,
            row.purpose.value,
            purpose.value,
        )
        return InvalidAuthTokenError("This link is invalid.")

    if row.consumed_at is not None:
        logger.info(
            "AUTH_TOKEN_REJECTED | token=%s | reason=already_consumed", row.id
        )
        return InvalidAuthTokenError("This link has already been used.")

    if row.invalidated_at is not None:
        logger.info(
            "AUTH_TOKEN_REJECTED | token=%s | reason=invalidated (%s)",
            row.id,
            row.invalidated_reason or "unspecified",
        )
        return InvalidAuthTokenError("This link is no longer valid.")

    if row.expires_at <= now:
        logger.info("AUTH_TOKEN_REJECTED | token=%s | reason=expired", row.id)
        return ExpiredAuthTokenError("This link has expired.")

    # Every condition in the UPDATE's WHERE clause is satisfied, yet it matched
    # nothing. Reachable only if a concurrent transaction consumed the row
    # between the UPDATE and this SELECT, which is the double-submit case the
    # conditional UPDATE exists to handle.
    logger.info("AUTH_TOKEN_REJECTED | token=%s | reason=concurrent_consume", row.id)
    return InvalidAuthTokenError("This link has already been used.")


# ===========================================================================
# Invalidation
# ===========================================================================

def invalidate_outstanding(
    db: Session,
    *,
    user_id: uuid.UUID,
    purpose: AuthTokenPurpose,
    reason: str,
) -> int:
    """
    Withdraws every unconsumed token of one purpose for one user.

    Called on success, not on issuance. When a password reset completes, every
    other outstanding reset link for that account stops working — so a link
    that reached an attacker's copy of the mailbox dies the moment the real
    owner completes their own reset. Same on verification: once an address is
    proved, the other outstanding verification links have nothing left to
    prove.

    Invalidated is distinct from consumed and both are checked at consumption.
    Consumed means the user spent it; invalidated means the system withdrew it.
    Collapsing them would make an incident unreadable.

    Returns:
        The number of tokens invalidated.
    """
    now = datetime.now(UTC)

    # synchronize_session="fetch" so instances already loaded in this session
    # see the invalidation. Without it a caller holding an AuthToken object
    # would keep reading invalidated_at as None after this returns.
    result = db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user_id,
            AuthToken.purpose == purpose,
            AuthToken.consumed_at.is_(None),
            AuthToken.invalidated_at.is_(None),
        )
        .values(invalidated_at=now, invalidated_reason=reason[:100])
        .execution_options(synchronize_session="fetch")
    )
    db.flush()

    if result.rowcount:
        logger.info(
            "AUTH_TOKEN_INVALIDATED | user=%s | purpose=%s | count=%d | reason=%s",
            user_id,
            purpose.value,
            result.rowcount,
            reason,
        )
    return result.rowcount


# ===========================================================================
# Housekeeping (R8)
# ===========================================================================

def sweep_expired_tokens(
    db: Session,
    *,
    retain_days: int = 30,
) -> int:
    """
    Deletes tokens well past expiry.

    Retained past expiry rather than deleted at it, because a consumed row is
    the evidence that a verification or reset actually completed, and "this
    token was already used" is a materially different answer to a confused user
    than "this token never existed".

    Returns:
        The number of rows deleted.
    """
    cutoff = datetime.now(UTC) - timedelta(days=retain_days)

    rows = list(
        db.scalars(select(AuthToken).where(AuthToken.expires_at < cutoff)).all()
    )
    for row in rows:
        db.delete(row)
    db.flush()

    if rows:
        logger.info(
            "AUTH_TOKEN_SWEEP | deleted=%d | expired before %s",
            len(rows),
            cutoff.isoformat(),
        )
    return len(rows)
