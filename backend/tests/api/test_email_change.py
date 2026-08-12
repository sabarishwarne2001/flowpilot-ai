"""
ARCH-06 Step 9 — email change HTTP endpoints & redirect validation tests.
"""

from __future__ import annotations

import pytest

from app.services import email_change_service as ecs


REQUEST_URL = "/api/v1/me/email-change/request"
CONFIRM_URL = "/api/v1/auth/email-change/confirm"
PERSONA_PASSWORD = "test-password"


@pytest.fixture()
def token_spy(monkeypatch):
    captured: dict[str, str] = {}
    original = ecs.secrets.token_urlsafe

    def spy(n: int) -> str:
        token = original(n)
        captured["token"] = token
        return token

    monkeypatch.setattr(ecs.secrets, "token_urlsafe", spy)
    return captured


# ===========================================================================
# Request
# ===========================================================================

class TestRequestEndpoint:

    def test_unauthenticated_is_refused(self, client):
        response = client.post(
            REQUEST_URL,
            json={"current_password": "x", "new_email": "a@example.com"},
        )
        assert response.status_code == 401

    def test_wrong_password_is_403_not_401(self, client, tenant):
        response = client.post(
            REQUEST_URL,
            json={
                "current_password": "definitely-not-the-password",
                "new_email": "moved@example.com",
            },
            headers=tenant.ws_admin.headers,
        )
        assert response.status_code == 403

    def test_success_is_202_and_returns_no_token(self, client, tenant):
        response = client.post(
            REQUEST_URL,
            json={
                "current_password": PERSONA_PASSWORD,
                "new_email": "moved@example.com",
            },
            headers=tenant.ws_admin.headers,
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["new_email"] == "moved@example.com"
        assert "token" not in response.text.lower()
        assert "#token=" not in response.text

    def test_requesting_the_current_address_is_400(self, client, tenant):
        response = client.post(
            REQUEST_URL,
            json={
                "current_password": PERSONA_PASSWORD,
                "new_email": tenant.ws_admin.user.email,
            },
            headers=tenant.ws_admin.headers,
        )
        assert response.status_code == 400

    def test_requesting_a_taken_address_is_409(self, client, tenant):
        response = client.post(
            REQUEST_URL,
            json={
                "current_password": PERSONA_PASSWORD,
                "new_email": tenant.viewer.user.email,
            },
            headers=tenant.ws_admin.headers,
        )
        assert response.status_code == 409


# ===========================================================================
# Cancel
# ===========================================================================

class TestCancelEndpoint:

    def test_cancel_with_nothing_pending_is_404(self, client, tenant):
        response = client.delete(
            REQUEST_URL, headers=tenant.ws_admin.headers
        )
        assert response.status_code == 404

    def test_cancel_returns_204_and_kills_the_link(
        self, client, tenant, token_spy
    ):
        client.post(
            REQUEST_URL,
            json={
                "current_password": PERSONA_PASSWORD,
                "new_email": "withdrawn@example.com",
            },
            headers=tenant.ws_admin.headers,
        )

        cancelled = client.delete(
            REQUEST_URL, headers=tenant.ws_admin.headers
        )
        assert cancelled.status_code == 204
        assert cancelled.content == b""

        confirmed = client.post(
            CONFIRM_URL, json={"token": token_spy["token"]}
        )
        assert confirmed.status_code == 400

    def test_unauthenticated_cancel_is_refused(self, client):
        assert client.delete(REQUEST_URL).status_code == 401


# ===========================================================================
# Confirm
# ===========================================================================

class TestConfirmEndpoint:

    def test_confirm_requires_no_session(self, client, tenant, token_spy):
        client.post(
            REQUEST_URL,
            json={
                "current_password": PERSONA_PASSWORD,
                "new_email": "confirmed@example.com",
            },
            headers=tenant.ws_admin.headers,
        )

        response = client.post(
            CONFIRM_URL, json={"token": token_spy["token"]}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["email"] == "confirmed@example.com"
        assert body["sessions_revoked"] is True

    def test_garbage_token_is_400_and_names_no_cause(self, client):
        response = client.post(CONFIRM_URL, json={"token": "not-a-real-token"})

        assert response.status_code == 400
        detail = response.json()["detail"].lower()
        assert "invalid" in detail

    def test_replaying_a_consumed_token_is_400(
        self, client, tenant, token_spy
    ):
        client.post(
            REQUEST_URL,
            json={
                "current_password": PERSONA_PASSWORD,
                "new_email": "once@example.com",
            },
            headers=tenant.ws_admin.headers,
        )

        first = client.post(CONFIRM_URL, json={"token": token_spy["token"]})
        assert first.status_code == 200

        second = client.post(CONFIRM_URL, json={"token": token_spy["token"]})
        assert second.status_code == 400

    def test_confirming_signs_every_session_out(
        self, client, tenant, token_spy
    ):
        headers = tenant.ws_admin.headers

        client.post(
            REQUEST_URL,
            json={
                "current_password": PERSONA_PASSWORD,
                "new_email": "signedout@example.com",
            },
            headers=headers,
        )
        assert (
            client.post(
                CONFIRM_URL, json={"token": token_spy["token"]}
            ).status_code
            == 200
        )

        after = client.get("/api/v1/me/profile", headers=headers)
        assert after.status_code == 401


# ===========================================================================
# Registration redirect (§B.8)
# ===========================================================================

class TestRegistrationRedirectIsValidatedServerSide:

    @pytest.mark.parametrize(
        "redirect",
        [
            "https://evil.example.com",
            "//evil.example.com/path",
            "/\\evil.example.com",
            "not-absolute",
            "/x:y",
        ],
    )
    def test_unsafe_redirect_does_not_fail_registration(
        self, client, redirect
    ):
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"redir-{abs(hash(redirect))}@example.com",
                "password": "a-sufficiently-long-password",
                "redirect": redirect,
            },
        )
        assert response.status_code == 202, response.text

    def test_safe_redirect_is_accepted(self, client):
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "redir-safe@example.com",
                "password": "a-sufficiently-long-password",
                "redirect": "/acme/engineering/work-items",
            },
        )
        assert response.status_code == 202

    def test_registration_without_a_redirect_still_works(self, client):
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "redir-absent@example.com",
                "password": "a-sufficiently-long-password",
            },
        )
        assert response.status_code == 202