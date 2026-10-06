import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.rate_limit.policy import POLICY_LOGIN_ACCOUNT_IP
from app.services.login_backoff_service import (
    _pair_hmac,
    check_login_backoff,
    clear_login_backoff,
)


def test_login_backoff_engages_and_answers_like_a_wrong_password(
    client: TestClient, monkeypatch
):
    """Owner decision N-022 (F-096): a backed-off sign-in keeps the generic
    OWASP answer. The back-off engages (the service reports it), but the HTTP
    response is the same 401 a wrong password gets, with no Retry-After, so an
    attacker cannot tell a locked pair from a wrong guess."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)

    email = "testuser@example.com"
    ip = "testclient"

    clear_login_backoff(ip, email)
    clear_login_backoff("127.0.0.1", email)

    status_initial = check_login_backoff(ip, email)
    assert status_initial.is_backed_off is False

    first = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "wrongpassword"},
    )
    assert first.status_code == 401

    for _ in range(POLICY_LOGIN_ACCOUNT_IP.threshold + 2):
        refused = client.post(
            "/api/v1/auth/login",
            data={"username": email, "password": "wrongpassword"},
        )

    assert check_login_backoff(ip, email).is_backed_off is True
    assert refused.status_code == 401
    assert refused.json() == first.json()
    assert "retry-after" not in {key.lower() for key in refused.headers}

    clear_login_backoff(ip, email)
    clear_login_backoff("127.0.0.1", email)


def test_login_backoff_hmac_privacy():
    ip = "203.0.113.10"
    email = "SensitiveUser@Domain.Com"

    pair_hash = _pair_hmac(ip, email)
    assert email.lower() not in pair_hash
    assert "@" not in pair_hash
    assert len(pair_hash) == 32
