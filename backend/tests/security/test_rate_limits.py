"""Rate limits — the sign-in limiter refuses a guessing attacker, and fails closed.

    pytest tests/security/test_rate_limits.py -q

The test harness turns rate limiting off (`ENVIRONMENT=test` and `RATE_LIMIT_ENABLED=False`), so
nothing else in the suite proves the limiter does anything. These tests switch it on with the
real Redis backend the production stack uses (the in-process backend refuses to run outside
`ENVIRONMENT=test`, where the limiter is bypassed, so it cannot be exercised through the API),
and check what an attacker sees:

* password guessing from one address is cut off after a small number of attempts, with a 429
  and a `Retry-After`, and the refusal comes BEFORE the password is checked, so a correct guess
  after the cut-off does not get in;
* a forged `X-Forwarded-For` header does not give the attacker a fresh allowance;
* the sign-in limiter FAILS CLOSED: if the limiter's store is broken, sign-in is refused rather
  than left unlimited, while an ordinary route keeps working.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.rate_limit import limiter as limiter_module
from app.core.rate_limit.policy import POLICY_LOGIN_IP
from app.core.redis_client import get_redis_client
from tests.conftest import Fixture

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
TEST_PASSWORD = "test-password"  # what tests.conftest._make_user gives every persona


def _login(client: TestClient, email: str, password: str = "definitely-wrong", **headers: str):
    return client.post(LOGIN, data={"username": email, "password": password}, headers=headers)


def _fresh_address() -> str:
    return f"nobody-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture()
def limiter_on(monkeypatch: pytest.MonkeyPatch):
    """Rate limiting enabled against the real Redis backend, and cleaned up afterwards."""
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "RATE_LIMIT_BACKEND", "redis")
    limiter_module.reset_rate_limit_backend()

    redis = get_redis_client()
    if redis is None:
        pytest.skip("no Redis to test the production limiter backend against")
    try:
        redis.ping()
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"Redis is not reachable: {error}")

    def clean() -> None:
        for key in redis.scan_iter("rl:v1:*"):
            redis.delete(key)

    clean()
    yield
    clean()
    limiter_module.reset_rate_limit_backend()


def _exhaust(client: TestClient) -> tuple[int, object]:
    """Wrong sign-ins for fresh addresses until the limiter answers 429; (allowed, that response)."""
    allowed = 0
    for _ in range(POLICY_LOGIN_IP.limit + 5):
        response = _login(client, _fresh_address())
        if response.status_code == 429:
            return allowed, response
        assert response.status_code == 401, response.text
        allowed += 1
    pytest.fail("the sign-in limiter never answered 429")


def test_password_guessing_is_cut_off_with_a_429(client: TestClient, limiter_on: None) -> None:
    allowed, refused = _exhaust(client)
    print(f"sign-in attempts allowed before the cut-off: {allowed}")
    assert 3 <= allowed <= POLICY_LOGIN_IP.limit, f"{allowed} attempts allowed before the cut-off"
    assert int(refused.headers["Retry-After"]) >= 1
    assert refused.headers["RateLimit-Remaining"] == "0"


def test_the_cut_off_comes_before_the_password_is_checked(client: TestClient, tenant: Fixture, limiter_on: None) -> None:
    """Control first: the same correct sign-in works before the attacker has used the allowance."""
    assert _login(client, tenant.owner.user.email, TEST_PASSWORD).status_code == 200

    _exhaust(client)
    right_password_after_the_cut_off = _login(client, tenant.owner.user.email, TEST_PASSWORD)
    assert right_password_after_the_cut_off.status_code == 429
    assert "access_token" not in right_password_after_the_cut_off.text


def test_a_forged_forwarded_for_header_does_not_buy_a_fresh_allowance(client: TestClient, limiter_on: None) -> None:
    _exhaust(client)
    for forged in ("203.0.113.7", "198.51.100.23, 203.0.113.7", "::1"):
        assert _login(client, _fresh_address(), **{"X-Forwarded-For": forged}).status_code == 429, forged


def test_sign_in_fails_closed_when_the_limiter_store_is_broken(
    client: TestClient, tenant: Fixture, limiter_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken:
        def consume(self, **_: object):
            raise ConnectionError("the limiter store is down")

    monkeypatch.setattr(limiter_module, "_backend", Broken())
    assert _login(client, tenant.owner.user.email, TEST_PASSWORD).status_code == 429, "sign-in was left unlimited"
    # An ordinary route fails OPEN by design: a broken limiter must not take the whole API down.
    assert client.get(ME, headers=tenant.owner.headers).status_code == 200
