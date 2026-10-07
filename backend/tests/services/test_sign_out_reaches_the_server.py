"""F-130 — "Sign out" must end the session on the server, not only in the browser.

The refresh cookie was scoped to `Path=/api/v1/auth/refresh`, so the browser never
sent it to `POST /api/v1/auth/logout`. Sign-out answered 204 and its response
deleted the cookie in the browser, but the server never saw which session to end:
the session stayed live for its full 14 days, kept appearing in "Active sessions",
and a copy of the refresh token taken before sign-out kept working (ASVS V3.3.1).

The existing logout tests could not see it: they check that a refresh fails
afterwards, which it does, because the browser's cookie is gone.
"""

from __future__ import annotations

from app.core.cookies import REFRESH_COOKIE_NAME, refresh_cookie_path
from app.core.tokens import hash_token
from app.models.user_session import SessionRevokedReason, UserSession

PASSWORD = "correct-horse-battery-staple"


def _login(client, user) -> str:
    response = client.post("/api/v1/auth/login", data={"username": user.email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client.cookies.get(REFRESH_COOKIE_NAME)


def _row(db, plaintext: str) -> UserSession:
    db.expire_all()
    return db.query(UserSession).filter(UserSession.token_hash == hash_token(plaintext)).one()


def test_the_browser_sends_the_refresh_cookie_to_sign_out(client, registered):
    _login(client, registered)
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204
    assert REFRESH_COOKIE_NAME in response.request.headers.get("cookie", "")


def test_sign_out_revokes_the_session_on_the_server(client, registered, db):
    token = _login(client, registered)
    assert client.post("/api/v1/auth/logout").status_code == 204

    row = _row(db, token)
    assert row.revoked_at is not None
    assert row.revoked_reason is SessionRevokedReason.LOGOUT


def test_a_copy_of_the_refresh_token_stops_working_after_sign_out(client, second_client, registered):
    copied = _login(client, registered)
    assert client.post("/api/v1/auth/logout").status_code == 204

    second_client.cookies.set(REFRESH_COOKIE_NAME, copied, path=refresh_cookie_path())
    assert second_client.post("/api/v1/auth/refresh").status_code == 401


def test_the_cookie_still_does_not_reach_the_rest_of_the_api(client, registered):
    _login(client, registered)
    response = client.get("/api/v1/health")
    assert REFRESH_COOKIE_NAME not in response.request.headers.get("cookie", "")
