"""Signing every session out, live, with tokens from a real login.

Confirming an email change signs the account out everywhere: a token from a
real login (it carries its session id) is refused at once, and the user can
sign in again with the new address. The same holds for "sign out of all
devices".

tests/api/test_email_change.py::test_confirming_signs_every_session_out is
red on main for a test-only reason: its persona token carries no session id
and is minted in the same second as the revocation, and the per-user cut-off
is compared in whole seconds. Every token the product issues carries a
session id (auth._issue), and revoking all sessions revokes each session row.
"""

from __future__ import annotations

from app.services import email_change_service as ecs
from tests.engines.conftest import Engines

PASSWORD = "test-password"  # tests/conftest.py _make_user


def _login(engines: Engines, email: str) -> dict:
    response = engines.client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_confirming_an_email_change_signs_real_sessions_out(engines: Engines, monkeypatch) -> None:
    captured: dict[str, str] = {}
    original = ecs.secrets.token_urlsafe

    def spy(n: int) -> str:
        captured["token"] = original(n)
        return captured["token"]

    monkeypatch.setattr(ecs.secrets, "token_urlsafe", spy)
    email = engines.tenant.contributor.user.email
    laptop, phone = _login(engines, email), _login(engines, email)
    assert engines.client.get("/api/v1/me/profile", headers=phone).status_code == 200

    requested = engines.client.post("/api/v1/me/email-change/request", headers=laptop,
                                    json={"current_password": PASSWORD, "new_email": "moved@example.com"})
    assert requested.status_code in (200, 202), requested.text
    confirmed = engines.client.post("/api/v1/auth/email-change/confirm", json={"token": captured["token"]})
    assert confirmed.status_code == 200, confirmed.text

    for device in (laptop, phone):
        assert engines.client.get("/api/v1/me/profile", headers=device).status_code == 401
    assert engines.client.get("/api/v1/me/profile", headers=_login(engines, "moved@example.com")).status_code == 200


def test_sign_out_everywhere_refuses_every_real_token(engines: Engines) -> None:
    email = engines.tenant.viewer.user.email
    laptop, phone = _login(engines, email), _login(engines, email)
    out = engines.client.post("/api/v1/auth/logout-all", headers=laptop)
    assert out.status_code == 204, out.text
    for device in (laptop, phone):
        assert engines.client.get("/api/v1/me/profile", headers=device).status_code == 401
