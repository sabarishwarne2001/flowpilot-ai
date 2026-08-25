"""
Refresh session lifecycle for FlowPilot AI.
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
    """An already-rotated token was presented outside the grace window."""


# ===========================================================================
# Carriers
# ===========================================================================

@dataclass(frozen=True)
class IssuedSession:
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
    user: User | None = None,
    user_id: uuid.UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    family_id: uuid.UUID | None = None,
    authenticated_at: datetime | None = None,
    auth_method: str = "PASSWORD",
    idp_config_id: uuid.UUID | None = None,
    idp_session_index: str | None = None,
    pinned_ip: str | None = None,
    pinned_ip_prefix: int | None = None,
) -> IssuedSession:
    """
    Opens a new refresh session with full federation & IP pinning support.
    """
    if user is None:
        if user_id is None:
            raise ValueError("Either user or user_id must be provided to create_session")
        resolved_user_id = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
        user = db.get(User, resolved_user_id)
        if user is None:
            raise ValueError(f"User {user_id} does not exist")
    else:
        resolved_user_id = user.id

    plaintext = generate_secure_token()
    now = datetime.now(UTC)

    session = UserSession(
        user_id=resolved_user_id,
        family_id=family_id or uuid.uuid4(),
        token_hash=hash_token(plaintext),
        expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        ip_address=ip_address,
        user_agent=user_agent,
        authenticated_at=authenticated_at or now,
        auth_method=auth_method,
        idp_config_id=idp_config_id,
        idp_session_index=idp_session_index,
        pinned_ip=pinned_ip,
        pinned_ip_prefix=pinned_ip_prefix,
    )
    db.add(session)
    db.flush()

    logger.info(
        "SESSION_CREATED | user=%s | session=%s | family=%s | auth_method=%s",
        resolved_user_id,
        session.id,
        session.family_id,
        auth_method,
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
    current = session
    for _ in range(settings.SESSION_CHAIN_WALK_LIMIT):
        if current.replaced_by_id is None:
            return current
        successor = db.get(UserSession, current.replaced_by_id)
        if successor is None:
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
    now = datetime.now(UTC)
    session = get_session_by_token(db, refresh_token=refresh_token)

    if session is None:
        logger.info("SESSION_REFRESH_REJECTED | reason=no_matching_session")
        raise InvalidRefreshTokenError("Invalid refresh token.")

    if session.expires_at <= now:
        revoke_session(db, session=session, reason=SessionRevokedReason.EXPIRED)
        raise ExpiredRefreshTokenError("This session has expired.")

    if session.rotated_at is not None:
        return _handle_rotated_token_replay(
            db,
            session=session,
            now=now,
            ip_address=ip_address,
            user_agent=user_agent,
        )

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
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
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
        authenticated_at=session.authenticated_at,
        auth_method=session.auth_method,
        idp_config_id=session.idp_config_id,
        idp_session_index=session.idp_session_index,
        pinned_ip=session.pinned_ip,
        pinned_ip_prefix=session.pinned_ip_prefix,
    )

    session.rotated_at = now
    session.replaced_by_id = issued.session.id
    session.last_used_at = now
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

    if tip.revoked_at is not None and tip.revoked_reason is not SessionRevokedReason.ROTATED:
        raise RevokedRefreshTokenError("This session is no longer valid.")
    if tip.rotated_at is not None:
        revoke_family(
            db,
            family_id=session.family_id,
            reason=SessionRevokedReason.REUSE_DETECTED,
        )
        logger.warning(
            "SESSION_REUSE_DETECTED | user=%s | family=%s | chain walk did not reach an unrotated tip",
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
# Housekeeping
# ===========================================================================

def sweep_expired_sessions(
    db: Session,
    *,
    retain_days: int = 30,
) -> int:
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