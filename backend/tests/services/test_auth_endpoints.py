"""
ARCH-03 Step 7 — session lifecycle over HTTP.

Step 6 proved the services in isolation. These exercise the same logic through
the real request/response cycle, because everything that can go wrong in Step 7
goes wrong in the wiring rather than the logic: a cookie set at one Path and
cleared at another, a rotation committed on the failure branch, a 401 that
leaves the client holding a credential the server will never accept again.

TestClient keeps a cookie jar across requests, so the refresh loop here is the
same sequence a browser performs.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.core.cookies import REFRESH_COOKIE_NAME, refresh_cookie_path
from app.core.security import decode_access_token_claims
from app.models.user_session import SessionRevokedReason, UserSession

PASSWORD = "correct-horse-battery-staple"


# ===========================================================================
# Login
# ===========================================================================

def test_login_returns_a_token_and_sets_the_cookie(client, registered):
    response = _login(client, registered)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"

    cookie = _set_cookie_header(response)
    assert REFRESH_COOKIE_NAME in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie.lower().replace("samesite=lax", "SameSite=lax")
    # Scoped to the auth routes, so the long-lived credential is not attached
    # to every request to every endpoint.
    assert f"Path={refresh_cookie_path()}" in cookie


def test_access_token_carries_the_session_id(client, registered):
    body = _login(client, registered).json()
    claims = decode_access_token_claims(body["access_token"])

    assert claims is not None
    assert claims.session_id is not None
    # A ten-minute token, per §B.6 — short because refresh renews it silently.
    lifetime = (claims.expires_at - claims.issued_at).total_seconds() / 60
    assert abs(lifetime - settings.ACCESS_TOKEN_EXPIRE_MINUTES) < 1


def test_refresh_token_is_not_in_the_response_body(client, registered):
    body = _login(client, registered).text
    jar_value = client.cookies.get(REFRESH_COOKIE_NAME)

    assert jar_value
    # The whole split collapses if the long-lived credential is readable by
    # script. It must reach the client only as a Set-Cookie header.
    assert jar_value not in body


def test_bad_credentials_set_no_cookie(client, registered):
    response = client.post(
        "/api/v1/auth/login",
        data={"username": registered.email, "password": "wrong"},
    )
    assert response.status_code == 401
    assert REFRESH_COOKIE_NAME not in client.cookies


# ===========================================================================
# Refresh
# ===========================================================================

def test_refresh_rotates_the_cookie_and_issues_a_new_token(client, registered):
    first = _login(client, registered).json()["access_token"]
    first_cookie = client.cookies.get(REFRESH_COOKIE_NAME)

    response = client.post("/api/v1/auth/refresh")

    assert response.status_code == 200
    second = response.json()["access_token"]
    second_cookie = client.cookies.get(REFRESH_COOKIE_NAME)

    assert second != first
    assert second_cookie != first_cookie


def test_refresh_without_a_cookie_is_401(client):
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_refreshed_token_authenticates(client, registered):
    _login(client, registered)
    token = client.post("/api/v1/auth/refresh").json()["access_token"]

    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == registered.email


def test_a_long_refresh_chain_keeps_one_live_session(client, registered, db):
    _login(client, registered)
    for _ in range(5):
        assert client.post("/api/v1/auth/refresh").status_code == 200

    token = client.post("/api/v1/auth/refresh").json()["access_token"]
    listed = client.get(
        "/api/v1/auth/sessions", headers={"Authorization": f"Bearer {token}"}
    ).json()

    # One device, one row. Rotation revokes as it rotates.
    assert len(listed) == 1


# ===========================================================================
# Reuse detection over the wire
# ===========================================================================

def test_replaying_a_rotated_cookie_kills_the_family(client, registered, db):
    _login(client, registered)
    stolen = client.cookies.get(REFRESH_COOKIE_NAME)
    client.post("/api/v1/auth/refresh")

    # Age the rotation past the grace window so this reads as theft rather
    # than a tab race.
    _age_rotations(db, stolen, settings.SESSION_REUSE_GRACE_SECONDS + 5)

    client.cookies.set(REFRESH_COOKIE_NAME, stolen, path=refresh_cookie_path())
    response = client.post("/api/v1/auth/refresh")

    assert response.status_code == 401
    assert "reused" in response.json()["detail"].lower()
    # The revocation must survive the failed request. Rolling back here would
    # leave the replayed token working.
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_reuse_response_clears_the_cookie(client, registered, db):
    _login(client, registered)
    stolen = client.cookies.get(REFRESH_COOKIE_NAME)
    client.post("/api/v1/auth/refresh")
    _age_rotations(db, stolen, settings.SESSION_REUSE_GRACE_SECONDS + 5)

    client.cookies.set(REFRESH_COOKIE_NAME, stolen, path=refresh_cookie_path())
    response = client.post("/api/v1/auth/refresh")

    # Asserted on the header rather than the client's jar: the jar was seeded
    # by hand above with no domain, so it will not match a deletion scoped to
    # the server's. The header is the contract a real browser acts on.
    cookie = response.headers.get("set-cookie", "")
    assert REFRESH_COOKIE_NAME in cookie
    assert 'Max-Age=0' in cookie or "01 Jan 1970" in cookie
    # Path must match what set it, or the browser keeps the original and the
    # client retries a dead credential forever.
    assert f"Path={refresh_cookie_path()}" in cookie


def test_concurrent_refresh_inside_grace_is_served(client, registered):
    _login(client, registered)
    original = client.cookies.get(REFRESH_COOKIE_NAME)
    assert client.post("/api/v1/auth/refresh").status_code == 200

    # No ageing: this is the second tab, milliseconds behind the first.
    client.cookies.set(REFRESH_COOKIE_NAME, original, path=refresh_cookie_path())
    second = client.post("/api/v1/auth/refresh")

    assert second.status_code == 200
    # R2: the user stays signed in.
    token = second.json()["access_token"]
    assert (
        client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 200
    )


# ===========================================================================
# Sign out
# ===========================================================================

def test_logout_ends_the_session_and_clears_the_cookie(client, registered):
    _login(client, registered)

    assert client.post("/api/v1/auth/logout").status_code == 204
    assert not client.cookies.get(REFRESH_COOKIE_NAME)
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_logout_works_without_an_access_token(client, registered):
    """
    A user whose access token expired must still be able to sign out.

    If logout required authentication, an expired token would leave a live
    fourteen-day refresh session behind — the opposite of what was asked for.
    """
    _login(client, registered)
    assert client.post("/api/v1/auth/logout").status_code == 204


def test_logout_is_idempotent(client, registered):
    _login(client, registered)
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/logout").status_code == 204


def test_logout_all_revokes_every_device(client, second_client, registered, db):
    laptop = _login(client, registered).json()["access_token"]
    _login(second_client, registered)

    assert client.post(
        "/api/v1/auth/logout-all",
        headers={"Authorization": f"Bearer {laptop}"},
    ).status_code == 204

    # The other device cannot refresh.
    assert second_client.post("/api/v1/auth/refresh").status_code == 401


def test_logout_all_invalidates_access_tokens_already_in_flight(
    client, second_client, registered
):
    """
    The reason sessions_revoked_at exists (§B.6).

    Access tokens are stateless; revoking session rows alone would leave them
    valid for up to the full access TTL after the user asked to be signed out.

    The sleep is not padding. deps compares at whole-second granularity on
    purpose (see _token_predates_revocation), so a token issued in the same
    second as the revocation survives it — for less than one second, while the
    session rows are already revoked and no refresh is possible. Without the
    sleep this test measures that boundary instead of the mechanism, and the
    boundary is asserted separately below.
    """
    laptop = _login(client, registered).json()["access_token"]
    phone = _login(second_client, registered).json()["access_token"]

    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {phone}"}
    ).status_code == 200

    time.sleep(1.1)
    client.post(
        "/api/v1/auth/logout-all",
        headers={"Authorization": f"Bearer {laptop}"},
    )

    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {phone}"}
    ).status_code == 401


def test_revocation_cutoff_is_whole_second_granularity(client, registered, db):
    """
    Documents the accepted cost of the whole-second comparison.

    A token issued in the same wall-clock second as a revocation survives it.
    That is deliberate: comparing integer `iat` against a microsecond timestamp
    would instead reject tokens issued in the sub-second tail *after* a
    revocation, which is exactly what "change password, stay signed in"
    produces.

    The cutoff is written directly rather than raced for. Relying on two
    requests landing in the same second makes the outcome depend on how long
    bcrypt took, which is how a test starts failing on a slower machine for
    reasons unrelated to what it is testing.
    """
    token = _login(client, registered).json()["access_token"]
    issued_at = decode_access_token_claims(token).issued_at

    # 900ms after the token's second began: later than iat, same second.
    registered.sessions_revoked_at = datetime.fromtimestamp(
        int(issued_at.timestamp()), tz=UTC
    ) + timedelta(milliseconds=900)
    db.commit()

    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200

    # One second later there is no ambiguity, and the token is rejected.
    registered.sessions_revoked_at = issued_at + timedelta(seconds=1)
    db.commit()

    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 401


# ===========================================================================
# Device list
# ===========================================================================

def test_sessions_lists_devices_without_leaking_credentials(
    client, second_client, registered
):
    token = _login(client, registered).json()["access_token"]
    _login(second_client, registered)

    listed = client.get(
        "/api/v1/auth/sessions", headers={"Authorization": f"Bearer {token}"}
    ).json()

    assert len(listed) == 2
    for row in listed:
        assert "token" not in row
        assert "token_hash" not in row
        assert {"id", "created_at", "expires_at"} <= set(row)


def test_revoking_one_session_leaves_the_others(
    client, second_client, registered
):
    laptop = _login(client, registered).json()["access_token"]
    _login(second_client, registered)

    listed = client.get(
        "/api/v1/auth/sessions", headers={"Authorization": f"Bearer {laptop}"}
    ).json()
    phone_id = [
        row["id"]
        for row in listed
        if row["id"] != str(decode_access_token_claims(laptop).session_id)
    ][0]

    assert client.delete(
        f"/api/v1/auth/sessions/{phone_id}",
        headers={"Authorization": f"Bearer {laptop}"},
    ).status_code == 204

    assert second_client.post("/api/v1/auth/refresh").status_code == 401
    # This device is untouched — that is the difference from logout-all.
    assert client.post("/api/v1/auth/refresh").status_code == 200


def test_another_users_session_is_404_not_403(
    client, second_client, registered, other_registered
):
    mine = _login(client, registered).json()["access_token"]
    _login(second_client, other_registered)
    theirs = decode_access_token_claims(
        _login(second_client, other_registered).json()["access_token"]
    ).session_id

    # 403 would confirm the identifier names a real session on another
    # account. 404 says only that the caller has no such session.
    assert client.delete(
        f"/api/v1/auth/sessions/{theirs}",
        headers={"Authorization": f"Bearer {mine}"},
    ).status_code == 404


# ===========================================================================
# Helpers
# ===========================================================================

def _login(client, user):
    return client.post(
        "/api/v1/auth/login",
        data={"username": user.email, "password": PASSWORD},
    )


def _set_cookie_header(response) -> str:
    return response.headers.get("set-cookie", "")


def _age_rotations(db, plaintext_cookie: str, seconds: int) -> None:
    """Backdates a rotation so the grace window has demonstrably elapsed."""
    from app.core.tokens import hash_token

    row = (
        db.query(UserSession)
        .filter(UserSession.token_hash == hash_token(plaintext_cookie))
        .one()
    )
    row.rotated_at = datetime.now(UTC) - timedelta(seconds=seconds)
    db.commit()