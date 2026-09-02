"""
ARCH-03 Step 6 — session service.

The rotation and reuse-detection tests are the reason this file exists. That
logic has three branches whose difference is a ten-second interval, and none of
them can be exercised through the API until Step 7. Testing them here means the
endpoints in Step 7 are wiring rather than discovery.

Where a test needs a token to look older than it is, it edits rotated_at
directly rather than sleeping. Ten seconds per assertion would put this file
past a minute, and a suite slow enough to skip is a suite that stops catching
things.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.core.tokens import generate_secure_token, hash_token
from app.models.user_session import SessionRevokedReason, UserSession
from app.services import session_service as svc


# ===========================================================================
# Creation
# ===========================================================================

def test_create_session_stores_only_the_hash(db, user):
    issued = svc.create_session(db, user=user, ip_address="203.0.113.9")

    assert issued.plaintext_token
    assert issued.session.token_hash == hash_token(issued.plaintext_token)
    assert issued.plaintext_token != issued.session.token_hash

    # The secret must not be recoverable from the row under any column.
    stored = {
        c.name: getattr(issued.session, c.name)
        for c in UserSession.__table__.columns
    }
    assert issued.plaintext_token not in [
        v for v in stored.values() if isinstance(v, str)
    ]


def test_create_session_starts_a_new_family(db, user):
    first = svc.create_session(db, user=user)
    second = svc.create_session(db, user=user)
    assert first.family_id != second.family_id


def test_create_session_honours_configured_ttl(db, user):
    issued = svc.create_session(db, user=user)
    expected = datetime.now(UTC) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    assert abs((issued.session.expires_at - expected).total_seconds()) < 5


# ===========================================================================
# Normal rotation
# ===========================================================================

def test_rotation_issues_a_new_token_in_the_same_family(db, user):
    first = svc.create_session(db, user=user)
    second = svc.rotate_session(db, refresh_token=first.plaintext_token)

    assert second.plaintext_token != first.plaintext_token
    assert second.family_id == first.family_id
    assert second.session.id != first.session.id


def test_rotation_marks_the_predecessor_rotated_and_linked(db, user):
    first = svc.create_session(db, user=user)
    second = svc.rotate_session(db, refresh_token=first.plaintext_token)
    db.refresh(first.session)

    assert first.session.rotated_at is not None
    assert first.session.replaced_by_id == second.session.id
    assert first.session.revoked_reason is SessionRevokedReason.ROTATED


def test_rotated_predecessor_leaves_the_device_list(db, user):
    first = svc.create_session(db, user=user)
    assert len(svc.list_active_sessions(db, user=user)) == 1

    second = svc.rotate_session(db, refresh_token=first.plaintext_token)
    live = svc.list_active_sessions(db, user=user)

    # One device, one row — not one row per link in the chain.
    assert [s.id for s in live] == [second.session.id]


def test_rotation_chain_survives_many_hops(db, user):
    issued = svc.create_session(db, user=user)
    family = issued.family_id
    for _ in range(5):
        issued = svc.rotate_session(db, refresh_token=issued.plaintext_token)

    assert issued.family_id == family
    assert len(svc.list_active_sessions(db, user=user)) == 1


# ===========================================================================
# Rejections
# ===========================================================================

def test_unknown_token_is_rejected(db, user):
    with pytest.raises(svc.InvalidRefreshTokenError):
        svc.rotate_session(db, refresh_token=generate_secure_token())


def test_stored_hash_is_not_itself_a_credential(db, user):
    issued = svc.create_session(db, user=user)
    with pytest.raises(svc.InvalidRefreshTokenError):
        svc.rotate_session(db, refresh_token=issued.session.token_hash)


def test_expired_session_is_rejected_and_marked(db, user):
    issued = svc.create_session(db, user=user)
    issued.session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()

    with pytest.raises(svc.ExpiredRefreshTokenError):
        svc.rotate_session(db, refresh_token=issued.plaintext_token)

    db.refresh(issued.session)
    assert issued.session.revoked_reason is SessionRevokedReason.EXPIRED


def test_logged_out_session_is_rejected(db, user):
    issued = svc.create_session(db, user=user)
    svc.revoke_session(
        db, session=issued.session, reason=SessionRevokedReason.LOGOUT
    )

    with pytest.raises(svc.RevokedRefreshTokenError):
        svc.rotate_session(db, refresh_token=issued.plaintext_token)


def test_deactivated_account_cannot_rotate(db, user):
    issued = svc.create_session(db, user=user)
    user.is_active = False
    db.flush()

    with pytest.raises(svc.RevokedRefreshTokenError):
        svc.rotate_session(db, refresh_token=issued.plaintext_token)

    db.refresh(issued.session)
    assert issued.session.revoked_reason is SessionRevokedReason.ACCOUNT_DISABLED


# ===========================================================================
# Reuse detection — the theft path
# ===========================================================================

def _age_rotation(db, session: UserSession, seconds: int) -> None:
    """Backdates a rotation so the grace window has demonstrably elapsed."""
    session.rotated_at = datetime.now(UTC) - timedelta(seconds=seconds)
    db.flush()


def test_replay_outside_grace_revokes_the_whole_family(db, user):
    first = svc.create_session(db, user=user)
    second = svc.rotate_session(db, refresh_token=first.plaintext_token)
    third = svc.rotate_session(db, refresh_token=second.plaintext_token)

    db.refresh(first.session)
    _age_rotation(db, first.session, settings.SESSION_REUSE_GRACE_SECONDS + 5)

    with pytest.raises(svc.SessionReuseDetectedError):
        svc.rotate_session(db, refresh_token=first.plaintext_token)

    # Every link, including the one the legitimate user is holding.
    for issued in (first, second, third):
        db.refresh(issued.session)
        assert issued.session.revoked_at is not None
    assert third.session.revoked_reason is SessionRevokedReason.REUSE_DETECTED
    assert svc.list_active_sessions(db, user=user) == []


def test_reuse_detection_does_not_touch_other_families(db, user):
    victim = svc.create_session(db, user=user)
    bystander = svc.create_session(db, user=user)
    svc.rotate_session(db, refresh_token=victim.plaintext_token)

    db.refresh(victim.session)
    _age_rotation(db, victim.session, settings.SESSION_REUSE_GRACE_SECONDS + 5)

    with pytest.raises(svc.SessionReuseDetectedError):
        svc.rotate_session(db, refresh_token=victim.plaintext_token)

    # The user's other device stays signed in. Reuse is a property of a chain,
    # not of an account.
    db.refresh(bystander.session)
    assert bystander.session.revoked_at is None
    assert [s.id for s in svc.list_active_sessions(db, user=user)] == [
        bystander.session.id
    ]


def test_the_successor_stops_working_after_reuse_is_detected(db, user):
    first = svc.create_session(db, user=user)
    second = svc.rotate_session(db, refresh_token=first.plaintext_token)

    db.refresh(first.session)
    _age_rotation(db, first.session, settings.SESSION_REUSE_GRACE_SECONDS + 5)

    with pytest.raises(svc.SessionReuseDetectedError):
        svc.rotate_session(db, refresh_token=first.plaintext_token)

    # Whoever holds the live token is now locked out too. That is the intended
    # outcome: both parties descend from one login and cannot be told apart.
    with pytest.raises(svc.RevokedRefreshTokenError):
        svc.rotate_session(db, refresh_token=second.plaintext_token)


# ===========================================================================
# Concurrent refresh — the tab-race path (R2)
# ===========================================================================

def test_replay_inside_grace_is_served_not_punished(db, user):
    first = svc.create_session(db, user=user)
    second = svc.rotate_session(db, refresh_token=first.plaintext_token)

    # No ageing: the rotation just happened, so this is inside the window.
    third = svc.rotate_session(db, refresh_token=first.plaintext_token)

    assert third.family_id == first.family_id
    db.refresh(second.session)
    # The family survives. This is the assertion R2 is about.
    assert svc.list_active_sessions(db, user=user)


def test_grace_path_mints_a_fresh_token_rather_than_the_successor(db, user):
    first = svc.create_session(db, user=user)
    second = svc.rotate_session(db, refresh_token=first.plaintext_token)
    third = svc.rotate_session(db, refresh_token=first.plaintext_token)

    # The successor's plaintext was handed to the first caller and never
    # stored, so it cannot be returned again. A new one is minted instead.
    assert third.plaintext_token != second.plaintext_token
    assert third.session.id != second.session.id


def test_many_tabs_racing_converge_on_one_chain(db, user):
    first = svc.create_session(db, user=user)
    results = [
        svc.rotate_session(db, refresh_token=first.plaintext_token)
        for _ in range(4)
    ]

    assert len({r.family_id for r in results}) == 1
    # Rotating the tip rather than the presented row is what keeps this at one
    # live session instead of four forked branches.
    assert len(svc.list_active_sessions(db, user=user)) == 1


def test_grace_boundary_is_the_configured_window(db, user):
    first = svc.create_session(db, user=user)
    svc.rotate_session(db, refresh_token=first.plaintext_token)
    db.refresh(first.session)

    _age_rotation(db, first.session, settings.SESSION_REUSE_GRACE_SECONDS - 1)
    svc.rotate_session(db, refresh_token=first.plaintext_token)  # inside

    db.refresh(first.session)
    _age_rotation(db, first.session, settings.SESSION_REUSE_GRACE_SECONDS + 1)
    with pytest.raises(svc.SessionReuseDetectedError):
        svc.rotate_session(db, refresh_token=first.plaintext_token)


def test_grace_path_will_not_resurrect_a_revoked_family(db, user):
    first = svc.create_session(db, user=user)
    svc.rotate_session(db, refresh_token=first.plaintext_token)
    svc.revoke_family(
        db,
        family_id=first.family_id,
        reason=SessionRevokedReason.PASSWORD_CHANGE,
    )

    # Still inside the grace window, but the family is dead. Serving here would
    # hand out a working token from a chain that was deliberately closed.
    with pytest.raises(svc.SessionError):
        svc.rotate_session(db, refresh_token=first.plaintext_token)


# ===========================================================================
# Global revocation (§B.6)
# ===========================================================================

def test_revoke_all_closes_every_session_and_stamps_the_cutoff(db, user):
    a = svc.create_session(db, user=user)
    b = svc.create_session(db, user=user)
    assert user.sessions_revoked_at is None

    count = svc.revoke_all_user_sessions(
        db, user=user, reason=SessionRevokedReason.PASSWORD_CHANGE
    )

    assert count == 2
    assert user.sessions_revoked_at is not None
    for issued in (a, b):
        db.refresh(issued.session)
        assert issued.session.revoked_reason is SessionRevokedReason.PASSWORD_CHANGE
    assert svc.list_active_sessions(db, user=user) == []


def test_revoke_all_is_scoped_to_one_user(db, user, other_user):
    svc.create_session(db, user=user)
    theirs = svc.create_session(db, user=other_user)

    svc.revoke_all_user_sessions(
        db, user=user, reason=SessionRevokedReason.LOGOUT_ALL
    )

    db.refresh(theirs.session)
    assert theirs.session.revoked_at is None
    assert other_user.sessions_revoked_at is None


def test_revocation_reason_is_not_overwritten(db, user):
    issued = svc.create_session(db, user=user)
    svc.revoke_session(
        db, session=issued.session, reason=SessionRevokedReason.REUSE_DETECTED
    )
    first_stamp = issued.session.revoked_at

    svc.revoke_session(
        db, session=issued.session, reason=SessionRevokedReason.LOGOUT
    )

    # The first reason is the true one. Overwriting it would erase an incident.
    assert issued.session.revoked_reason is SessionRevokedReason.REUSE_DETECTED
    assert issued.session.revoked_at == first_stamp


# ===========================================================================
# Housekeeping (R8)
# ===========================================================================

def test_sweep_removes_only_long_expired_rows(db, user):
    fresh = svc.create_session(db, user=user)
    stale = svc.create_session(db, user=user)
    recently_expired = svc.create_session(db, user=user)

    stale.session.expires_at = datetime.now(UTC) - timedelta(days=40)
    recently_expired.session.expires_at = datetime.now(UTC) - timedelta(days=2)
    db.flush()

    deleted = svc.sweep_expired_sessions(db, retain_days=30)

    assert deleted == 1
    assert db.get(UserSession, stale.session.id) is None
    assert db.get(UserSession, fresh.session.id) is not None
    # Retained: a revoked row is the evidence of an incident, and an
    # investigation a week later needs the chain intact.
    assert db.get(UserSession, recently_expired.session.id) is not None


def test_sweeping_a_successor_does_not_delete_its_ancestor(db, user):
    first = svc.create_session(db, user=user)
    second = svc.rotate_session(db, refresh_token=first.plaintext_token)

    second.session.expires_at = datetime.now(UTC) - timedelta(days=40)
    db.flush()
    svc.sweep_expired_sessions(db, retain_days=30)

    # replaced_by_id is ON DELETE SET NULL, not CASCADE.
    ancestor = db.get(UserSession, first.session.id)
    assert ancestor is not None
    db.refresh(ancestor)
    assert ancestor.replaced_by_id is None
