"""
ARCH-03 Step 6 — single-use identity tokens.

The single-use guarantee is enforced by the WHERE clause of one UPDATE, not by
a lock or a read-then-write, so the tests that matter most are the ones that
try to spend a token twice and the one that tries to spend a verification token
as a password reset.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.core.tokens import generate_secure_token, hash_token
from app.models.auth_token import AuthToken, AuthTokenPurpose
from app.services import auth_token_service as svc

VERIFY = AuthTokenPurpose.EMAIL_VERIFICATION
RESET = AuthTokenPurpose.PASSWORD_RESET


# ===========================================================================
# Issuance
# ===========================================================================

def test_issue_stores_only_the_hash(db, user):
    issued = svc.issue_token(db, user=user, purpose=VERIFY)

    assert issued.auth_token.token_hash == hash_token(issued.plaintext_token)
    stored = [
        getattr(issued.auth_token, c.name)
        for c in AuthToken.__table__.columns
    ]
    assert issued.plaintext_token not in [v for v in stored if isinstance(v, str)]


def test_ttl_differs_by_purpose(db, user):
    verify = svc.issue_token(db, user=user, purpose=VERIFY)
    reset = svc.issue_token(db, user=user, purpose=RESET)

    now = datetime.now(UTC)
    verify_hours = (verify.auth_token.expires_at - now).total_seconds() / 3600
    reset_minutes = (reset.auth_token.expires_at - now).total_seconds() / 60

    assert abs(verify_hours - settings.EMAIL_VERIFICATION_TTL_HOURS) < 0.1
    assert abs(reset_minutes - settings.PASSWORD_RESET_TTL_MINUTES) < 1
    # A reset link is a password-equivalent credential in a mailbox; a
    # verification link grants nothing on its own.
    assert reset.auth_token.expires_at < verify.auth_token.expires_at


def test_issuing_does_not_invalidate_earlier_tokens(db, user):
    first = svc.issue_token(db, user=user, purpose=RESET)
    svc.issue_token(db, user=user, purpose=RESET)

    # Both links are equally legitimate. A user who requests a second reset
    # because the first email was slow must not find the first one dead.
    db.refresh(first.auth_token)
    assert first.auth_token.invalidated_at is None
    assert svc.consume_token(db, token=first.plaintext_token, purpose=RESET)


def test_rate_limit_applies_per_purpose(db, user):
    for _ in range(settings.IDENTITY_TOKEN_MAX_PER_WINDOW):
        svc.issue_token(db, user=user, purpose=RESET)

    with pytest.raises(svc.AuthTokenRateLimitError):
        svc.issue_token(db, user=user, purpose=RESET)

    # A flood of reset requests must not block the user from verifying.
    assert svc.issue_token(db, user=user, purpose=VERIFY)


def test_rate_limit_is_scoped_to_one_user(db, user, other_user):
    for _ in range(settings.IDENTITY_TOKEN_MAX_PER_WINDOW):
        svc.issue_token(db, user=user, purpose=RESET)

    assert svc.issue_token(db, user=other_user, purpose=RESET)


def test_rate_limit_can_be_bypassed_deliberately(db, user):
    for _ in range(settings.IDENTITY_TOKEN_MAX_PER_WINDOW):
        svc.issue_token(db, user=user, purpose=RESET)

    assert svc.issue_token(
        db, user=user, purpose=RESET, enforce_rate_limit=False
    )


# ===========================================================================
# Consumption
# ===========================================================================

def test_consume_marks_the_row_and_returns_it(db, user):
    issued = svc.issue_token(db, user=user, purpose=VERIFY)

    row = svc.consume_token(db, token=issued.plaintext_token, purpose=VERIFY)

    assert row.id == issued.auth_token.id
    assert row.user_id == user.id
    assert row.consumed_at is not None


def test_a_token_cannot_be_spent_twice(db, user):
    issued = svc.issue_token(db, user=user, purpose=VERIFY)
    svc.consume_token(db, token=issued.plaintext_token, purpose=VERIFY)

    with pytest.raises(svc.InvalidAuthTokenError):
        svc.consume_token(db, token=issued.plaintext_token, purpose=VERIFY)


def test_a_verification_token_cannot_reset_a_password(db, user):
    issued = svc.issue_token(db, user=user, purpose=VERIFY)

    # Both purposes are 256-bit secrets in one table. Without purpose in the
    # WHERE clause this would succeed.
    with pytest.raises(svc.InvalidAuthTokenError):
        svc.consume_token(db, token=issued.plaintext_token, purpose=RESET)

    # And the token survives its misuse, so the real flow still works.
    assert svc.consume_token(db, token=issued.plaintext_token, purpose=VERIFY)


def test_unknown_token_is_rejected(db, user):
    with pytest.raises(svc.InvalidAuthTokenError):
        svc.consume_token(db, token=generate_secure_token(), purpose=VERIFY)


def test_stored_hash_is_not_itself_a_credential(db, user):
    issued = svc.issue_token(db, user=user, purpose=VERIFY)
    with pytest.raises(svc.InvalidAuthTokenError):
        svc.consume_token(db, token=issued.auth_token.token_hash, purpose=VERIFY)


def test_expired_token_reports_expiry_distinctly(db, user):
    issued = svc.issue_token(db, user=user, purpose=RESET)
    issued.auth_token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()

    # A distinct error so the API can offer a resend instead of sending a user
    # with an ordinary expired link to support.
    with pytest.raises(svc.ExpiredAuthTokenError):
        svc.consume_token(db, token=issued.plaintext_token, purpose=RESET)


def test_invalidated_token_is_rejected(db, user):
    issued = svc.issue_token(db, user=user, purpose=RESET)
    svc.invalidate_outstanding(
        db, user_id=user.id, purpose=RESET, reason="test"
    )

    with pytest.raises(svc.InvalidAuthTokenError):
        svc.consume_token(db, token=issued.plaintext_token, purpose=RESET)


def test_consumption_rolls_back_with_its_caller(db, user):
    """
    The consumption participates in the caller's transaction.

    If the surrounding work fails — the new password will not hash, the commit
    is rejected — the token must still be spendable, or a transient error costs
    the user their only reset link.
    """
    issued = svc.issue_token(db, user=user, purpose=RESET)
    db.commit()

    savepoint = db.begin_nested()
    svc.consume_token(db, token=issued.plaintext_token, purpose=RESET)
    savepoint.rollback()

    assert svc.consume_token(db, token=issued.plaintext_token, purpose=RESET)


# ===========================================================================
# Invalidation
# ===========================================================================

def test_invalidate_outstanding_clears_only_live_tokens(db, user):
    consumed = svc.issue_token(db, user=user, purpose=RESET)
    live_a = svc.issue_token(db, user=user, purpose=RESET)
    live_b = svc.issue_token(db, user=user, purpose=RESET)
    svc.consume_token(db, token=consumed.plaintext_token, purpose=RESET)

    count = svc.invalidate_outstanding(
        db, user_id=user.id, purpose=RESET, reason="password reset completed"
    )

    assert count == 2
    for issued in (live_a, live_b):
        db.refresh(issued.auth_token)
        assert issued.auth_token.invalidated_at is not None

    # Consumed and invalidated stay distinct. Collapsing them would make an
    # incident review unable to tell "the user used it" from "we withdrew it".
    db.refresh(consumed.auth_token)
    assert consumed.auth_token.consumed_at is not None
    assert consumed.auth_token.invalidated_at is None


def test_invalidate_is_scoped_to_purpose_and_user(db, user, other_user):
    mine_reset = svc.issue_token(db, user=user, purpose=RESET)
    mine_verify = svc.issue_token(db, user=user, purpose=VERIFY)
    theirs = svc.issue_token(db, user=other_user, purpose=RESET)

    svc.invalidate_outstanding(
        db, user_id=user.id, purpose=RESET, reason="reset completed"
    )

    db.refresh(mine_reset.auth_token)
    db.refresh(mine_verify.auth_token)
    db.refresh(theirs.auth_token)
    assert mine_reset.auth_token.invalidated_at is not None
    assert mine_verify.auth_token.invalidated_at is None
    assert theirs.auth_token.invalidated_at is None


def test_completing_a_reset_kills_a_link_sent_to_a_stolen_mailbox(db, user):
    """
    The scenario invalidate_outstanding exists for.

    An attacker requests a reset and holds the link. The real owner requests
    their own and completes it. The attacker's link must stop working.
    """
    attacker_link = svc.issue_token(db, user=user, purpose=RESET)
    owner_link = svc.issue_token(db, user=user, purpose=RESET)

    svc.consume_token(db, token=owner_link.plaintext_token, purpose=RESET)
    svc.invalidate_outstanding(
        db, user_id=user.id, purpose=RESET, reason="password reset completed"
    )

    with pytest.raises(svc.InvalidAuthTokenError):
        svc.consume_token(db, token=attacker_link.plaintext_token, purpose=RESET)


# ===========================================================================
# Housekeeping (R8)
# ===========================================================================

def test_sweep_removes_only_long_expired_tokens(db, user):
    fresh = svc.issue_token(db, user=user, purpose=VERIFY)
    stale = svc.issue_token(db, user=user, purpose=VERIFY)
    recent = svc.issue_token(db, user=user, purpose=VERIFY)

    stale.auth_token.expires_at = datetime.now(UTC) - timedelta(days=40)
    recent.auth_token.expires_at = datetime.now(UTC) - timedelta(days=2)
    db.flush()

    deleted = svc.sweep_expired_tokens(db, retain_days=30)

    assert deleted == 1
    assert db.get(AuthToken, stale.auth_token.id) is None
    assert db.get(AuthToken, fresh.auth_token.id) is not None
    # "Already used" and "never existed" are different answers to a confused
    # user, so consumed rows outlive their expiry.
    assert db.get(AuthToken, recent.auth_token.id) is not None
