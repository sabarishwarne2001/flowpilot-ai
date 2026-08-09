"""
ARCH-03 Step 9 — password reset and change.

The tests that matter here are not the happy paths. They are:

  - that /forgot-password is indistinguishable for a real and a fake address,
    because that endpoint takes an arbitrary address from an anonymous caller
  - that the three side effects all fire, because a password change that only
    changes the hash is cosmetic
  - that an access token issued before the change is dead immediately, not at
    the end of its own TTL
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import verify_password
from app.core.tokens import generate_secure_token, hash_token
from app.models.auth_token import AuthToken, AuthTokenPurpose
from app.models.user_session import SessionRevokedReason, UserSession
from app.services import auth_token_service, password_service

RESET = AuthTokenPurpose.PASSWORD_RESET
VERIFY = AuthTokenPurpose.EMAIL_VERIFICATION
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"


# ===========================================================================
# /auth/forgot-password — the oracle tests
# ===========================================================================

def test_forgot_password_is_identical_for_unknown_addresses(
    client, registered
):
    real = client.post(
        "/api/v1/auth/forgot-password", json={"email": registered.email}
    )
    fake = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": f"nobody-{uuid.uuid4().hex[:8]}@example.com"},
    )

    # Any observable difference turns this into a membership oracle: paste in
    # a list of addresses, learn which have accounts here.
    assert real.status_code == fake.status_code == 202
    assert real.json() == fake.json()


def test_forgot_password_issues_a_token_for_a_real_account(
    client, registered, db
):
    client.post(
        "/api/v1/auth/forgot-password", json={"email": registered.email}
    )

    assert (
        db.query(AuthToken)
        .filter(
            AuthToken.user_id == registered.id, AuthToken.purpose == RESET
        )
        .count()
        == 1
    )


def test_forgot_password_is_case_and_whitespace_insensitive(
    client, registered, db
):
    client.post(
        "/api/v1/auth/forgot-password",
        json={"email": f"  {registered.email.upper()}  "},
    )

    assert (
        db.query(AuthToken)
        .filter(AuthToken.user_id == registered.id)
        .count()
        == 1
    )


def test_forgot_password_sends_nothing_for_an_inactive_account(
    client, registered, db
):
    registered.is_active = False
    db.commit()

    response = client.post(
        "/api/v1/auth/forgot-password", json={"email": registered.email}
    )

    # A deactivated account must not be recoverable, and the response must not
    # say so.
    assert response.status_code == 202
    assert (
        db.query(AuthToken).filter(AuthToken.user_id == registered.id).count()
        == 0
    )


def test_the_rate_limit_does_not_leak_through_the_response(
    client, registered, db
):
    from app.core.config import settings

    for _ in range(settings.IDENTITY_TOKEN_MAX_PER_WINDOW + 3):
        response = client.post(
            "/api/v1/auth/forgot-password", json={"email": registered.email}
        )
        # 429 here would answer "this account exists and someone has been
        # asking about it" — the opposite of what this endpoint must reveal.
        assert response.status_code == 202

    assert (
        db.query(AuthToken).filter(AuthToken.user_id == registered.id).count()
        <= settings.IDENTITY_TOKEN_MAX_PER_WINDOW
    )


def test_reset_link_puts_the_token_in_the_fragment():
    link = password_service.build_reset_link("sample-token")

    before_fragment = link.split("#", 1)[0]
    assert "token=" not in before_fragment
    assert "?" not in before_fragment
    assert "#token=sample-token" in link


# ===========================================================================
# /auth/reset-password
# ===========================================================================

def test_reset_changes_the_password(client, registered, db):
    plaintext = _issue_reset(db, registered)

    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": plaintext, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 200
    db.refresh(registered)
    assert verify_password(NEW_PASSWORD, registered.hashed_password)
    assert not verify_password(PASSWORD, registered.hashed_password)


def test_the_new_password_works_and_the_old_one_does_not(
    client, registered, db
):
    plaintext = _issue_reset(db, registered)
    client.post(
        "/api/v1/auth/reset-password",
        json={"token": plaintext, "new_password": NEW_PASSWORD},
    )

    assert _login_status(client, registered.email, PASSWORD) == 401
    assert _login_status(client, registered.email, NEW_PASSWORD) == 200


def test_a_reset_token_cannot_be_used_twice(client, registered, db):
    plaintext = _issue_reset(db, registered)
    client.post(
        "/api/v1/auth/reset-password",
        json={"token": plaintext, "new_password": NEW_PASSWORD},
    )

    assert client.post(
        "/api/v1/auth/reset-password",
        json={"token": plaintext, "new_password": "yet-another-passphrase"},
    ).status_code == 400


def test_expired_reset_token_says_so(client, registered, db):
    plaintext = _issue_reset(db, registered)
    row = (
        db.query(AuthToken)
        .filter(AuthToken.token_hash == hash_token(plaintext))
        .one()
    )
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": plaintext, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 400
    assert "expired" in response.json()["detail"].lower()


def test_a_verification_token_cannot_reset_a_password(
    client, registered, db
):
    issued = auth_token_service.issue_token(
        db, user=registered, purpose=VERIFY, enforce_rate_limit=False
    )
    db.commit()

    assert client.post(
        "/api/v1/auth/reset-password",
        json={"token": issued.plaintext_token, "new_password": NEW_PASSWORD},
    ).status_code == 400
    db.refresh(registered)
    assert verify_password(PASSWORD, registered.hashed_password)


def test_resetting_to_the_same_password_is_refused_and_the_link_survives(
    client, registered, db
):
    plaintext = _issue_reset(db, registered)

    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": plaintext, "new_password": PASSWORD},
    )
    assert response.status_code == 400

    # The consumption rolled back with the refusal, so the user can try again
    # with a password that actually differs.
    assert client.post(
        "/api/v1/auth/reset-password",
        json={"token": plaintext, "new_password": NEW_PASSWORD},
    ).status_code == 200


def test_reset_issues_no_session(client, registered, db):
    plaintext = _issue_reset(db, registered)

    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": plaintext, "new_password": NEW_PASSWORD},
    )

    # Completing a reset does not sign you in. An unauthenticated endpoint that
    # mints a refresh cookie, reachable with a link sitting in a mailbox, is
    # one credential path more than this flow needs.
    assert "access_token" not in response.json()
    assert not client.cookies.get("flowpilot_refresh")


def test_reset_verifies_an_unverified_address(client, unverified, db):
    plaintext = _issue_reset(db, unverified)

    client.post(
        "/api/v1/auth/reset-password",
        json={"token": plaintext, "new_password": NEW_PASSWORD},
    )

    # The token reached the address on this account and nowhere else —
    # forgot-password looks the user up by that address (§B.4).
    db.refresh(unverified)
    assert unverified.email_verified_at is not None


# ===========================================================================
# The three side effects
# ===========================================================================

def test_reset_revokes_every_session(client, second_client, registered, db):
    _login(client, registered)
    _login(second_client, registered)
    plaintext = _issue_reset(db, registered)

    client.post(
        "/api/v1/auth/reset-password",
        json={"token": plaintext, "new_password": NEW_PASSWORD},
    )

    live = (
        db.query(UserSession)
        .filter(
            UserSession.user_id == registered.id,
            UserSession.revoked_at.is_(None),
        )
        .count()
    )
    assert live == 0
    assert second_client.post("/api/v1/auth/refresh").status_code == 401


def test_reset_kills_access_tokens_already_in_flight(
    client, second_client, registered, db
):
    """
    The reason sessions_revoked_at exists.

    An attacker holding a stolen access token does not care what the password
    becomes. Revoking session rows alone leaves that token working for up to
    the full access TTL after the password protecting it was replaced.
    """
    stolen = _login(second_client, registered)
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {stolen}"}
    ).status_code == 200

    import time

    time.sleep(1.1)  # clear the whole-second cutoff boundary, see Step 7
    plaintext = _issue_reset(db, registered)
    client.post(
        "/api/v1/auth/reset-password",
        json={"token": plaintext, "new_password": NEW_PASSWORD},
    )

    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {stolen}"}
    ).status_code == 401


def test_reset_invalidates_every_other_outstanding_link(
    client, registered, db
):
    """
    The scenario this exists for.

    An attacker with read access to the mailbox requests a reset and holds the
    link. The real owner requests their own and completes it. The attacker's
    link must be dead.
    """
    attacker_link = _issue_reset(db, registered)
    owner_link = _issue_reset(db, registered)

    client.post(
        "/api/v1/auth/reset-password",
        json={"token": owner_link, "new_password": NEW_PASSWORD},
    )

    assert client.post(
        "/api/v1/auth/reset-password",
        json={"token": attacker_link, "new_password": "third-passphrase-here"},
    ).status_code == 400


def test_reset_stamps_the_revocation_reason(client, registered, db):
    _login(client, registered)
    plaintext = _issue_reset(db, registered)

    client.post(
        "/api/v1/auth/reset-password",
        json={"token": plaintext, "new_password": NEW_PASSWORD},
    )

    row = (
        db.query(UserSession)
        .filter(UserSession.user_id == registered.id)
        .first()
    )
    assert row.revoked_reason is SessionRevokedReason.PASSWORD_CHANGE
    db.refresh(registered)
    assert registered.sessions_revoked_at is not None


# ===========================================================================
# /auth/change-password
# ===========================================================================

def test_change_password_requires_authentication(client):
    assert client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    ).status_code == 401


def test_change_password_requires_the_current_password(client, registered):
    token = _login(client, registered)

    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "not-it", "new_password": NEW_PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    )

    # An access token is a bearer credential that may have been taken. Asking
    # for the password is what stops a stolen session locking the owner out.
    assert response.status_code == 400
    assert "incorrect" in response.json()["detail"].lower()


def test_change_password_rejects_an_unchanged_password(client, registered):
    token = _login(client, registered)

    assert client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 400


def test_change_password_keeps_this_device_signed_in(client, registered):
    token = _login(client, registered)

    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    fresh = response.json()["access_token"]
    assert fresh != token
    # This is the whole-second cutoff comparison from Step 7 paying off: the
    # revocation and this token land in the same wall-clock second.
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {fresh}"}
    ).status_code == 200


def test_change_password_signs_out_every_other_device(
    client, second_client, registered
):
    import time

    token = _login(client, registered)
    other = _login(second_client, registered)

    # Past the whole-second revocation boundary (see Step 7), so this measures
    # the mechanism rather than which side of a second the logins landed on.
    time.sleep(1.1)
    client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert second_client.post("/api/v1/auth/refresh").status_code == 401
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {other}"}
    ).status_code == 401


def test_change_password_rotates_the_refresh_cookie(client, registered):
    token = _login(client, registered)
    before = client.cookies.get("flowpilot_refresh")

    client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    )

    after = client.cookies.get("flowpilot_refresh")
    assert after and after != before
    assert client.post("/api/v1/auth/refresh").status_code == 200


def test_change_password_invalidates_outstanding_reset_links(
    client, registered, db
):
    outstanding = _issue_reset(db, registered)
    token = _login(client, registered)

    client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert client.post(
        "/api/v1/auth/reset-password",
        json={"token": outstanding, "new_password": "third-passphrase-here"},
    ).status_code == 400


def test_an_unverified_user_may_change_their_password(client, unverified):
    """
    Verification governs tenant access, not account self-management (§B.4).

    Gating this would leave an unverified user unable to respond to a password
    they believe is compromised.
    """
    token = _login(client, unverified)

    assert client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 200


def test_changing_a_password_does_not_verify_the_address(
    client, unverified, db
):
    """
    Only the reset path verifies.

    A change is proved by knowing the password, which says nothing about who
    reads the mailbox. Verification requires something that arrived by email.
    """
    token = _login(client, unverified)

    client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    )

    db.refresh(unverified)
    assert unverified.email_verified_at is None


# ===========================================================================
# Helpers
# ===========================================================================

def _login(client, user) -> str:
    return client.post(
        "/api/v1/auth/login",
        data={"username": user.email, "password": PASSWORD},
    ).json()["access_token"]


def _login_status(client, email: str, password: str) -> int:
    return client.post(
        "/api/v1/auth/login", data={"username": email, "password": password}
    ).status_code


def _issue_reset(db, user) -> str:
    issued = auth_token_service.issue_token(
        db, user=user, purpose=RESET, enforce_rate_limit=False
    )
    db.commit()
    return issued.plaintext_token