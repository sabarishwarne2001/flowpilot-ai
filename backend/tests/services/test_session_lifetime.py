"""ASVS V3.3.2 (Level 2) — a session needs signing in again after 12 hours, or 30 idle minutes.

Before this release each refresh slid the session forward 14 days with no absolute limit, so a
session used every day never asked for the password (or the second factor) again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.models.user_session import SessionRevokedReason
from app.services import session_service as svc


@pytest.fixture(autouse=True)
def _level_two_limits(monkeypatch):
    monkeypatch.setattr(settings, "SESSION_ABSOLUTE_LIFETIME_HOURS", 12)
    monkeypatch.setattr(settings, "SESSION_IDLE_TIMEOUT_MINUTES", 30)
    monkeypatch.setattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 10)


def test_an_active_session_refreshes(db, user):
    first = svc.create_session(db, user=user)
    second = svc.rotate_session(db, refresh_token=first.plaintext_token)
    assert second.session.family_id == first.session.family_id


def test_twelve_hours_after_sign_in_the_session_ends_however_active(db, user):
    issued = svc.create_session(db, user=user, authenticated_at=datetime.now(UTC) - timedelta(hours=12, minutes=1))
    with pytest.raises(svc.ExpiredRefreshTokenError):
        svc.rotate_session(db, refresh_token=issued.plaintext_token)
    db.refresh(issued.session)
    assert issued.session.revoked_reason is SessionRevokedReason.EXPIRED


def test_thirty_idle_minutes_end_the_session(db, user):
    issued = svc.create_session(db, user=user)
    # Last refresh 41 minutes ago: 30 idle minutes after the 10-minute access token ran out.
    issued.session.created_at = datetime.now(UTC) - timedelta(minutes=41)
    db.flush()
    with pytest.raises(svc.ExpiredRefreshTokenError):
        svc.rotate_session(db, refresh_token=issued.plaintext_token)


def test_a_short_pause_keeps_the_session(db, user):
    issued = svc.create_session(db, user=user)
    issued.session.created_at = datetime.now(UTC) - timedelta(minutes=35)
    db.flush()
    svc.rotate_session(db, refresh_token=issued.plaintext_token)


def test_zero_turns_the_limits_off(db, user, monkeypatch):
    monkeypatch.setattr(settings, "SESSION_ABSOLUTE_LIFETIME_HOURS", 0)
    monkeypatch.setattr(settings, "SESSION_IDLE_TIMEOUT_MINUTES", 0)
    issued = svc.create_session(db, user=user, authenticated_at=datetime.now(UTC) - timedelta(days=3))
    issued.session.created_at = datetime.now(UTC) - timedelta(days=2)
    db.flush()
    svc.rotate_session(db, refresh_token=issued.plaintext_token)
