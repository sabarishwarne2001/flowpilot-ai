"""
Refresh session lifecycle for FlowPilot AI.

One sessions row is one refresh token. The plaintext is a 256-bit secret that
exists in the HttpOnly cookie and nowhere else; this module stores only its
SHA-256 and therefore cannot reissue a token it has already handed out. That
constraint shapes the whole design, and §"Concurrent refresh" below is where it
bites hardest.

Access tokens are not recorded. Per-request revocation is obtained by comparing
the token's `iat` against users.sessions_revoked_at, which is already loaded on
the User row — a session lookup on every request would cost a query for the
same answer.

ROTATION AND REUSE DETECTION (§B.7)
-----------------------------------
    login          → row A, family F, live
    refresh with A → row B in family F; A is marked rotated and revoked
                     (ROTATED), A.replaced_by_id = B
    refresh with A → A is already rotated. Two possibilities, and the whole
                     difficulty of this module is telling them apart:

                       - a stolen token is being replayed
                       - two browser tabs refreshed at the same moment

Outside the grace window the presentation is treated as theft and the entire
family is revoked, which signs the user out of that device chain. That is the
correct response: the legitimate holder and the attacker both hold tokens
descended from the same login, so there is no way to keep one without keeping
the other.

Inside the window it is treated as a tab race and served. Getting this wrong in
the safe direction is not free — R2 names concurrent-refresh false positives as
the main operational risk of the phase, because the symptom is a user being
signed out at random with nothing in the logs that looks like an error.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.tokens import generate_secure_token, hash_token
from app.models.user import User
from app.models.user_session import SessionRevokedReason, UserSession

logger = logging.getLogger("app.services.session")


# ===========================================================================
# Errors
# ===========================================================================

class SessionError(Exception):
    """Base class for refresh failures."""


class InvalidRefreshTokenError(SessionError):
    """No live session matches the presented token."""


class ExpiredRefreshTokenError(SessionError):
    """The session matched but is past its expiry."""


class RevokedRefreshTokenError(SessionError):
    """The session matched but was revoked for a reason other than rotation."""


class SessionReuseDetectedError(SessionError):
    """
    An already-rotated token was presented outside the grace window.

    Raised *after* the family has been revoked, not before. The revocation is
    the response; the exception only reports it.
    """


# ===========================================================================
# Carriers
# ===========================================================================

@dataclass(frozen=True)
class IssuedSession:
    """
    A session row together with the plaintext refresh token addressing it.

    The plaintext is returned rather than stored, exactly as with invitations.
    This is the only moment the secret exists on the server; the caller must
    put it in the cookie before it goes out of scope.
    """

    session: UserSession
    plaintext_token: str

    @property
    def session_id(self) -> uuid.UUID:
        return self.session.id

    @property
    def family_id(self) -> uuid.UUID:
        return self.session.family_id


# ===========================================================================
# Issuance
# ===========================================================================

def create_session(
    db: Session,
    *,
    user: User,
    ip_address: str | None = None,
    user_agent: str | None = None,
    family_id: uuid.UUID | None = None,
) -> IssuedSession:
    """
    Opens a new refresh session.

    Called at login with no family_id, which starts a new chain, and by
    rotation with the parent's family_id, which continues one.

    Participates in the caller's transaction. Does not commit: a session
    created for a login that then fails its own checks must disappear with the
    rest of that request.

    Args:
        user: The account the session belongs to.
        ip_address: Request origin, recorded at issuance only.
        user_agent: Client string, rendered as a device label.
        family_id: Continues an existing chain. Omit to begin one.

    Returns:
        The session and its plaintext refresh token.
    """
    plaintext = generate_secure_token()
    now = datetime.now(UTC)

    session = UserSession(
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        token_hash=hash_token(plaintext),
        expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(session)
    db.flush()

    logger.info(
        "SESSION_CREATED | user=%s | session=%s | family=%s",
        user.id,
        session.id,
        session.family_id,
    )
    return IssuedSession(session=session, plaintext_token=plaintext)


# ===========================================================================
# Lookup
# ===========================================================================

def get_session_by_token(
    db: Session,
    *,
    refresh_token: str,
) -> UserSession | None:
    """
    Resolves a plaintext refresh token to its session row.

    Hashing happens here, at the boundary, for the same reason it happens in
    validate_invitation_token: below this line everything works in hashed
    terms and no query can be written against a plaintext that is not stored.

    Returns the row whatever its state — rotated, revoked, expired. Deciding
    what that state means is rotate_session's job, and a lookup that hid
    rotated rows would make reuse detection impossible.
    """
    return db.execute(
        select(UserSession).where(
            UserSession.token_hash == hash_token(refresh_token)
        )
    ).scalar_one_or_none()


def list_active_sessions(
    db: Session,
    *,
    user: User,
) -> list[UserSession]:
    """
    Lists the user's live sessions, newest first — the device list.

    Rotated rows are revoked with reason ROTATED, so this filter naturally
    returns one row per device rather than every link in every chain. That is
    what ix_sessions_user_revoked was built for.
    """
    now = datetime.now(UTC)
    return list(
        db.scalars(
            select(UserSession)
            .where(
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .order_by(UserSession.created_at.desc())
        ).all()
    )


# ===========================================================================
# Revocation
# ===========================================================================

def revoke_session(
    db: Session,
    *,
    session: UserSession,
    reason: SessionRevokedReason,
) -> UserSession:
    """
    Revokes one session. Idempotent: an already-revoked row keeps its original
    reason and timestamp, because the first reason is the true one and
    overwriting REUSE_DETECTED with LOGOUT would erase the incident.
    """
    if session.revoked_at is not None:
        return session

    session.revoked_at = datetime.now(UTC)
    session.revoked_reason = reason
    db.add(session)
    db.flush()

    logger.info(
        "SESSION_REVOKED | session=%s | user=%s | reason=%s",
        session.id,
        session.user_id,
        reason.value,
    )
    return session


def revoke_family(
    db: Session,
    *,
    family_id: uuid.UUID,
    reason: SessionRevokedReason,
) -> int:
    """
    Revokes every unrevoked session in one rotation chain.

    A single UPDATE rather than a loop over ORM objects. This runs on the
    reuse-detection path, where an attacker and the legitimate user are racing
    for the same chain; the fewer statements between detecting the problem and
    closing it, the smaller the window in which the attacker can rotate again.

    Returns:
        The number of rows revoked.
    """
    # synchronize_session="fetch" so session objects already loaded in this
    # transaction reflect the revocation. Rotation reads session.revoked_at
    # immediately after this on the reuse path, and a stale instance there
    # would let a revoked chain keep rotating.
    result = db.execute(
        update(UserSession)
        .where(
            UserSession.family_id == family_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
        .execution_options(synchronize_session="fetch")
    )
    db.flush()

    logger.warning(
        "SESSION_FAMILY_REVOKED | family=%s | reason=%s | sessions=%d",
        family_id,
        reason.value,
        result.rowcount,
    )
    return result.rowcount


def revoke_all_user_sessions(
    db: Session,
    *,
    user: User,
    reason: SessionRevokedReason,
) -> int:
    """
    Signs a user out everywhere, including outstanding access tokens.

    Two things happen and both are required. Revoking the session rows stops
    refresh. Stamping users.sessions_revoked_at stops the access tokens already
    in flight, which are stateless and would otherwise stay valid until their
    own expiry — up to the full access TTL after the user asked to be signed
    out (§B.6).

    Called on password change, password reset, and deactivation.

    Returns:
        The number of sessions revoked.
    """
    now = datetime.now(UTC)

    result = db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoked_reason=reason)
        .execution_options(synchronize_session="fetch")
    )

    # The cutoff is what deps compares each access token's iat against. Set
    # after the UPDATE but with the same timestamp, so no token minted during
    # this transaction can slip between the two operations.
    user.sessions_revoked_at = now
    db.add(user)
    db.flush()

    logger.info(
        "SESSION_ALL_REVOKED | user=%s | reason=%s | sessions=%d | cutoff=%s",
        user.id,
        reason.value,
        result.rowcount,
        now.isoformat(),
    )
    return result.rowcount


# ===========================================================================
# Rotation
# ===========================================================================

def _chain_tip(db: Session, session: UserSession) -> UserSession:
    """
    Walks replaced_by_id to the newest session in a chain.

    Bounded by SESSION_CHAIN_WALK_LIMIT. An unbounded walk over a cyclic or
    corrupt chain would hang the request thread, and the correct behaviour on
    data that cannot be true is to stop and let the caller treat it as a
    failure rather than to keep walking.
    """
    current = session
    for _ in range(settings.SESSION_CHAIN_WALK_LIMIT):
        if current.replaced_by_id is None:
            return current
        successor = db.get(UserSession, current.replaced_by_id)
        if successor is None:
            # The successor was swept. The chain ends here as far as this
            # request is concerned.
            return current
        current = successor
    return current


def rotate_session(
    db: Session,
    *,
    refresh_token: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> IssuedSession:
    """
    Exchanges a refresh token for a new one.

    Participates in the caller's transaction and does not commit, so a
    rotation and the access token minted alongside it either both happen or
    neither does.

    Raises:
        InvalidRefreshTokenError: No session matches.
        ExpiredRefreshTokenError: Past expiry.
        RevokedRefreshTokenError: Revoked for a reason other than rotation.
        SessionReuseDetectedError: Replay outside the grace window. The family
            has already been revoked when this is raised.
    """
    now = datetime.now(UTC)
    session = get_session_by_token(db, refresh_token=refresh_token)

    if session is None:
        # No family to revoke and nothing to log beyond the attempt: an
        # unmatched hash means either a forged token or one whose row has been
        # swept, and neither identifies a user.
        logger.info("SESSION_REFRESH_REJECTED | reason=no_matching_session")
        raise InvalidRefreshTokenError("Invalid refresh token.")

    if session.expires_at <= now:
        revoke_session(db, session=session, reason=SessionRevokedReason.EXPIRED)
        raise ExpiredRefreshTokenError("This session has expired.")

    # ----------------------------------------------------------------------
    # Replay of a rotated token
    # ----------------------------------------------------------------------
    if session.rotated_at is not None:
        return _handle_rotated_token_replay(
            db,
            session=session,
            now=now,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    # Revoked without having been rotated: logout, password change, admin
    # action, or a family already killed by reuse detection. Nothing to
    # salvage and nothing new to revoke.
    if session.revoked_at is not None:
        logger.info(
            "SESSION_REFRESH_REJECTED | session=%s | reason=%s",
            session.id,
            session.revoked_reason.value if session.revoked_reason else "unknown",
        )
        raise RevokedRefreshTokenError("This session is no longer valid.")

    return _rotate_live_session(
        db,
        session=session,
        now=now,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def _rotate_live_session(
    db: Session,
    *,
    session: UserSession,
    now: datetime,
    ip_address: str | None,
    user_agent: str | None,
) -> IssuedSession:
    """
    The normal path: a healthy token is exchanged for its successor.

    The successor inherits family_id and expires_at is recomputed from now, so
    an actively used session slides forward rather than expiring on a fixed
    schedule from the original login.
    """
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        # The FK is ON DELETE CASCADE so a missing user means the row is
        # mid-deletion; an inactive one must not be handed a fresh credential.
        revoke_session(
            db, session=session, reason=SessionRevokedReason.ACCOUNT_DISABLED
        )
        raise RevokedRefreshTokenError("This session is no longer valid.")

    issued = create_session(
        db,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
        family_id=session.family_id,
    )

    session.rotated_at = now
    session.replaced_by_id = issued.session.id
    session.last_used_at = now
    # Rotation revokes as well as rotates, so list_active_sessions can filter
    # on revoked_at alone and return one row per device instead of every link
    # in every chain.
    session.revoked_at = now
    session.revoked_reason = SessionRevokedReason.ROTATED
    db.add(session)
    db.flush()

    logger.info(
        "SESSION_ROTATED | user=%s | family=%s | from=%s | to=%s",
        user.id,
        session.family_id,
        session.id,
        issued.session.id,
    )
    return issued


def _handle_rotated_token_replay(
    db: Session,
    *,
    session: UserSession,
    now: datetime,
    ip_address: str | None,
    user_agent: str | None,
) -> IssuedSession:
    """
    Decides whether a replayed token is a tab race or a theft.

    CONCURRENT REFRESH, AND WHY A NEW TOKEN IS MINTED
    -------------------------------------------------
    The obvious handling of a tab race is to return the successor that the
    first request already created. That is impossible here, and the reason is
    the point of the whole design: the successor's plaintext was handed to the
    first caller and never stored, so the server cannot produce it again. There
    is nothing to return.

    So the grace path rotates the tip of the chain and mints a fresh token.
    Both tabs end up with working credentials, the loser's token is simply
    orphaned, and the chain grows by one extra link per concurrent request.
    That churn is the price of not storing plaintext, and it is a good trade:
    a handful of extra rows against never being able to reissue a secret.

    The tip is used rather than the presented row so that N tabs racing at once
    converge on one chain instead of forking it into N branches, each of which
    would then look like reuse to the others.

    Outside the window the family dies. Both the legitimate holder and the
    attacker hold tokens descended from one login, so there is no way to keep
    one and drop the other; signing the device out is the only response that
    does not leave the attacker inside.
    """
    grace = timedelta(seconds=settings.SESSION_REUSE_GRACE_SECONDS)
    age = now - session.rotated_at

    if age > grace:
        revoked = revoke_family(
            db,
            family_id=session.family_id,
            reason=SessionRevokedReason.REUSE_DETECTED,
        )
        logger.warning(
            "SESSION_REUSE_DETECTED | user=%s | family=%s | session=%s | "
            "rotated %.1fs ago | %d sessions revoked | ip=%s",
            session.user_id,
            session.family_id,
            session.id,
            age.total_seconds(),
            revoked,
            ip_address or "unknown",
        )
        raise SessionReuseDetectedError(
            "This session was signed out because its refresh token was reused."
        )

    tip = _chain_tip(db, session)

    # The tip is unusable if the family was revoked between the two requests,
    # or if the chain is corrupt enough that the walk ended somewhere already
    # rotated. Treat that as reuse rather than guessing: a grace path that
    # hands out a token from a revoked family defeats revocation.
    if tip.revoked_at is not None and tip.revoked_reason is not SessionRevokedReason.ROTATED:
        raise RevokedRefreshTokenError("This session is no longer valid.")
    if tip.rotated_at is not None:
        revoke_family(
            db,
            family_id=session.family_id,
            reason=SessionRevokedReason.REUSE_DETECTED,
        )
        logger.warning(
            "SESSION_REUSE_DETECTED | user=%s | family=%s | chain walk did not "
            "reach an unrotated tip",
            session.user_id,
            session.family_id,
        )
        raise SessionReuseDetectedError(
            "This session was signed out because its refresh token was reused."
        )

    logger.info(
        "SESSION_CONCURRENT_REFRESH | user=%s | family=%s | presented=%s | "
        "rotated %.2fs ago, within %ds grace | rotating tip=%s",
        session.user_id,
        session.family_id,
        session.id,
        age.total_seconds(),
        settings.SESSION_REUSE_GRACE_SECONDS,
        tip.id,
    )
    return _rotate_live_session(
        db,
        session=tip,
        now=now,
        ip_address=ip_address,
        user_agent=user_agent,
    )


# ===========================================================================
# Housekeeping (R8)
# ===========================================================================

def sweep_expired_sessions(
    db: Session,
    *,
    retain_days: int = 30,
) -> int:
    """
    Deletes sessions well past their expiry.

    Retained for a while after expiring rather than deleted at expiry, because
    a revoked row is the evidence of a reuse incident and an investigation
    that starts a week later needs the chain intact.

    replaced_by_id is ON DELETE SET NULL, so removing an old successor does not
    cascade into the ancestor that points at it.

    Returns:
        The number of rows deleted.
    """
    cutoff = datetime.now(UTC) - timedelta(days=retain_days)

    rows = list(
        db.scalars(
            select(UserSession).where(UserSession.expires_at < cutoff)
        ).all()
    )
    for row in rows:
        db.delete(row)
    db.flush()

    if rows:
        logger.info(
            "SESSION_SWEEP | deleted=%d | expired before %s",
            len(rows),
            cutoff.isoformat(),
        )
    return len(rows)