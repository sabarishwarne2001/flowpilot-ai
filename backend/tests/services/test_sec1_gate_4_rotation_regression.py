"""SEC-1 Gate 4 — dual-token rotation and family revocation, regression only."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core import security
from app.core.config import settings
from app.models.user import User
from app.models.user_session import SessionRevokedReason, UserSession
from app.services import session_service
from app.services.session_service import (
    ExpiredRefreshTokenError,
    InvalidRefreshTokenError,
    RevokedRefreshTokenError,
    SessionReuseDetectedError,
)


@pytest.fixture()
def account(db) -> User:
    user = User(
        email=f"sec1-rot-{uuid.uuid4().hex[:8]}@flowpilot.test",
        hashed_password=security.get_password_hash("a-perfectly-fine-password"),
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    return user


def family_rows(db, family_id: uuid.UUID) -> list[UserSession]:
    return (
        db.query(UserSession)
        .filter(UserSession.family_id == family_id)
        .order_by(UserSession.created_at)
        .all()
    )


# ============================================================================
# Reuse detection
# ============================================================================


class TestReuseDetection:
    def test_replaying_a_rotated_token_kills_the_family(self, db, account, monkeypatch):
        monkeypatch.setattr(settings, "SESSION_REUSE_GRACE_SECONDS", 0, raising=False)

        issued = session_service.create_session(db, user=account)
        stolen = issued.plaintext_token
        family = issued.family_id
        db.flush()

        legitimate = session_service.rotate_session(db, refresh_token=stolen)
        db.flush()

        with pytest.raises(SessionReuseDetectedError):
            session_service.rotate_session(db, refresh_token=stolen)
        db.flush()

        for row in family_rows(db, family):
            db.refresh(row)
            assert row.revoked_at is not None

        with pytest.raises((RevokedRefreshTokenError, SessionReuseDetectedError)):
            session_service.rotate_session(
                db, refresh_token=legitimate.plaintext_token
            )

    def test_revocation_reason_distinguishes_theft_from_logout(
        self, db, account, monkeypatch
    ):
        monkeypatch.setattr(settings, "SESSION_REUSE_GRACE_SECONDS", 0, raising=False)

        issued = session_service.create_session(db, user=account)
        stolen = issued.plaintext_token
        family = issued.family_id
        db.flush()
        session_service.rotate_session(db, refresh_token=stolen)
        db.flush()

        with pytest.raises(SessionReuseDetectedError):
            session_service.rotate_session(db, refresh_token=stolen)
        db.flush()

        reasons = set()
        for row in family_rows(db, family):
            db.refresh(row)
            if row.revoked_reason is not None:
                reasons.add(row.revoked_reason)

        assert SessionRevokedReason.REUSE_DETECTED in reasons
        assert SessionRevokedReason.LOGOUT not in reasons

    def test_reuse_in_one_family_does_not_touch_another(
        self, db, account, monkeypatch
    ):
        monkeypatch.setattr(settings, "SESSION_REUSE_GRACE_SECONDS", 0, raising=False)

        compromised = session_service.create_session(db, user=account)
        other_device = session_service.create_session(db, user=account)
        db.flush()

        session_service.rotate_session(
            db, refresh_token=compromised.plaintext_token
        )
        db.flush()
        with pytest.raises(SessionReuseDetectedError):
            session_service.rotate_session(
                db, refresh_token=compromised.plaintext_token
            )
        db.flush()

        survivor = session_service.rotate_session(
            db, refresh_token=other_device.plaintext_token
        )
        assert survivor.session_id is not None

    def test_unknown_token_is_rejected_without_naming_anybody(self, db):
        with pytest.raises(InvalidRefreshTokenError):
            session_service.rotate_session(db, refresh_token="not-a-real-token")

    def test_expired_token_is_rejected(self, db, account):
        issued = session_service.create_session(db, user=account)
        issued.session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.flush()

        with pytest.raises(ExpiredRefreshTokenError):
            session_service.rotate_session(db, refresh_token=issued.plaintext_token)


# ============================================================================
# The grace window
# ============================================================================


class TestConcurrentRefreshGrace:
    def test_tab_race_inside_the_grace_window_succeeds(
        self, db, account, monkeypatch
    ):
        monkeypatch.setattr(
            settings, "SESSION_REUSE_GRACE_SECONDS", 30, raising=False
        )

        issued = session_service.create_session(db, user=account)
        db.flush()

        first = session_service.rotate_session(
            db, refresh_token=issued.plaintext_token
        )
        db.flush()
        second = session_service.rotate_session(
            db, refresh_token=issued.plaintext_token
        )
        db.flush()

        assert first.session_id != second.session_id
        assert first.family_id == second.family_id

    def test_racing_tabs_converge_on_one_chain(self, db, account, monkeypatch):
        monkeypatch.setattr(
            settings, "SESSION_REUSE_GRACE_SECONDS", 30, raising=False
        )

        issued = session_service.create_session(db, user=account)
        family = issued.family_id
        db.flush()

        for _ in range(4):
            session_service.rotate_session(db, refresh_token=issued.plaintext_token)
            db.flush()

        rows = family_rows(db, family)
        unrotated = [row for row in rows if row.rotated_at is None]
        assert len(unrotated) == 1, "the chain forked instead of converging"

    def test_grace_path_never_mints_from_a_revoked_family(
        self, db, account, monkeypatch
    ):
        monkeypatch.setattr(
            settings, "SESSION_REUSE_GRACE_SECONDS", 30, raising=False
        )

        issued = session_service.create_session(db, user=account)
        db.flush()
        session_service.rotate_session(db, refresh_token=issued.plaintext_token)
        db.flush()

        session_service.revoke_family(
            db,
            family_id=issued.family_id,
            reason=SessionRevokedReason.PASSWORD_CHANGE,
        )
        db.flush()

        with pytest.raises((RevokedRefreshTokenError, SessionReuseDetectedError)):
            session_service.rotate_session(db, refresh_token=issued.plaintext_token)


# ============================================================================
# The SEC-1 interaction
# ============================================================================


class TestAuthenticatedAtSurvivesEveryPath:
    def test_grace_path_does_not_restamp_the_authentication_moment(
        self, db, account, monkeypatch
    ):
        monkeypatch.setattr(
            settings, "SESSION_REUSE_GRACE_SECONDS", 30, raising=False
        )

        stale = datetime.now(timezone.utc) - timedelta(days=200)
        issued = session_service.create_session(
            db, user=account, authenticated_at=stale
        )
        db.flush()

        first = session_service.rotate_session(
            db, refresh_token=issued.plaintext_token
        )
        db.flush()
        second = session_service.rotate_session(
            db, refresh_token=issued.plaintext_token
        )
        db.flush()

        assert first.session.authenticated_at == issued.session.authenticated_at
        assert second.session.authenticated_at == issued.session.authenticated_at

    def test_a_fresh_login_after_a_family_death_authenticates_anew(
        self, db, account, monkeypatch
    ):
        monkeypatch.setattr(settings, "SESSION_REUSE_GRACE_SECONDS", 0, raising=False)

        stale = datetime.now(timezone.utc) - timedelta(days=200)
        issued = session_service.create_session(
            db, user=account, authenticated_at=stale
        )
        db.flush()
        session_service.rotate_session(db, refresh_token=issued.plaintext_token)
        db.flush()
        with pytest.raises(SessionReuseDetectedError):
            session_service.rotate_session(db, refresh_token=issued.plaintext_token)
        db.flush()

        fresh = session_service.create_session(db, user=account)
        db.flush()
        assert fresh.session.authenticated_at > stale
        assert fresh.family_id != issued.family_id

    def test_every_link_in_a_family_carries_the_same_moment(
        self, db, account, monkeypatch
    ):
        monkeypatch.setattr(
            settings, "SESSION_REUSE_GRACE_SECONDS", 30, raising=False
        )

        stale = datetime.now(timezone.utc) - timedelta(days=90)
        issued = session_service.create_session(
            db, user=account, authenticated_at=stale
        )
        family = issued.family_id
        db.flush()

        token = issued.plaintext_token
        for _ in range(6):
            rotated = session_service.rotate_session(db, refresh_token=token)
            token = rotated.plaintext_token
            db.flush()

        moments = {row.authenticated_at for row in family_rows(db, family)}
        assert len(moments) == 1, "authenticated_at diverged within one family"


# ============================================================================
# Revocation reasons stay distinguishable
# ============================================================================


class TestRevocationReasons:
    def test_logout_does_not_overwrite_a_reuse_incident(
        self, db, account, monkeypatch
    ):
        monkeypatch.setattr(settings, "SESSION_REUSE_GRACE_SECONDS", 0, raising=False)

        issued = session_service.create_session(db, user=account)
        family = issued.family_id
        db.flush()
        session_service.rotate_session(db, refresh_token=issued.plaintext_token)
        db.flush()
        with pytest.raises(SessionReuseDetectedError):
            session_service.rotate_session(db, refresh_token=issued.plaintext_token)
        db.flush()

        session_service.revoke_family(
            db, family_id=family, reason=SessionRevokedReason.LOGOUT
        )
        db.flush()

        reasons = {
            row.revoked_reason for row in family_rows(db, family)
        }
        assert SessionRevokedReason.REUSE_DETECTED in reasons

    def test_all_documented_reasons_exist(self):
        for name in (
            "LOGOUT",
            "LOGOUT_ALL",
            "ROTATED",
            "REUSE_DETECTED",
            "PASSWORD_CHANGE",
            "EMAIL_CHANGE",
            "ACCOUNT_DISABLED",
            "EXPIRED",
        ):
            assert hasattr(SessionRevokedReason, name)