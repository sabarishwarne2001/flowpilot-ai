"""F-021 — a 422 must never repeat a submitted value back to the caller.

    pytest tests/security/test_validation_errors_do_not_echo_input.py -q

FastAPI's default 422 handler returns pydantic's error list verbatim, and each
entry carries ``input``, the exact value the caller sent for the failing field.
For a secret field (BYOK key, password, reset token) that copied the secret
into the response. The fix is one global handler, so these tests exercise
secret-bearing fields on several unrelated endpoints and the handler's shape,
not just the one BYOK field that first showed it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import Fixture

# Long enough to be unmistakable in a response body, and it fails the
# 8-character minimum of the password fields when cut short below.
SECRET_TOO_SHORT = "Sh0rt!7"  # 7 chars: below min_length=8
SECRET_TOO_LONG = "Zq9" * 60  # 180 chars: above max_length=128


def test_reset_password_422_does_not_echo_the_new_password(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "irrelevant", "new_password": SECRET_TOO_SHORT},
    )
    assert response.status_code == 422
    assert SECRET_TOO_SHORT not in response.text
    assert '"input"' not in response.text


def test_register_422_does_not_echo_an_overlong_password(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "someone@example.com", "password": SECRET_TOO_LONG},
    )
    assert response.status_code == 422
    assert SECRET_TOO_LONG not in response.text


def test_change_password_422_does_not_echo_the_current_password(
    client: TestClient, tenant: Fixture
) -> None:
    response = client.post(
        "/api/v1/auth/change-password",
        headers=tenant.owner.headers,
        json={"current_password": "old-secret-value", "new_password": SECRET_TOO_SHORT},
    )
    assert response.status_code == 422
    assert "old-secret-value" not in response.text
    assert SECRET_TOO_SHORT not in response.text


def test_the_422_keeps_the_default_shape_clients_read(client: TestClient) -> None:
    """Only the echoed value goes. `loc` and `msg` are what the console shows."""
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "irrelevant", "new_password": SECRET_TOO_SHORT},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list) and detail
    entry = detail[0]
    assert entry["loc"][-1] == "new_password"
    assert entry["type"] and entry["msg"]
    assert "input" not in entry


def test_a_missing_field_still_reports_which_field(client: TestClient) -> None:
    response = client.post("/api/v1/auth/reset-password", json={"token": "x"})
    assert response.status_code == 422
    assert any(e["loc"][-1] == "new_password" for e in response.json()["detail"])
