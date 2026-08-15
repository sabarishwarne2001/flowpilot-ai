import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.services.login_backoff_service import (
    _pair_hmac,
    check_login_backoff,
    clear_login_backoff,
)


def test_login_backoff_schedule_and_retry_after(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)

    email = "testuser@example.com"
    ip = "testclient"

    clear_login_backoff(ip, email)
    clear_login_backoff("127.0.0.1", email)

    status_initial = check_login_backoff(ip, email)
    assert status_initial.is_backed_off is False

    res1 = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "wrongpassword"},
    )
    assert res1.status_code == 401

    res2 = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "wrongpassword"},
    )
    assert res2.status_code == 429
    assert "Retry-After" in res2.headers
    assert res2.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"

    clear_login_backoff(ip, email)
    clear_login_backoff("127.0.0.1", email)


def test_login_backoff_hmac_privacy():
    ip = "203.0.113.10"
    email = "SensitiveUser@Domain.Com"

    pair_hash = _pair_hmac(ip, email)
    assert email.lower() not in pair_hash
    assert "@" not in pair_hash
    assert len(pair_hash) == 32