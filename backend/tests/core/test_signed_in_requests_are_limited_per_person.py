"""Signed-in people are rate limited per person, not per network address (campaign session 3, N-061).

The global limiter keyed every browser request by client IP: the middleware runs before sign-in is
resolved, so `request.state.user_id` was never set, and the per-user policy
(`RATE_LIMIT_USER_PER_MINUTE`) was declared and used by nothing. A whole company behind one office
address shared one allowance, while one person spread over many addresses had none. Now a request
carrying a valid access token is counted against that person; anonymous requests against the
address; API keys against the key, as before.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.rate_limit import limiter
from app.core.rate_limit.policy import RateLimitPolicy, RateLimitScope
from app.middleware import global_rate_limit
from tests.conftest import Fixture

LIMIT = 3


@pytest.fixture
def tight_limits(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_BACKEND", "redis")  # the real backend; names are unique per run
    limiter.reset_rate_limit_backend()
    run = uuid.uuid4().hex[:8]
    monkeypatch.setattr(global_rate_limit, "POLICY_GLOBAL_IP",
                        RateLimitPolicy(name=f"t_ip_{run}", scope=RateLimitScope.GLOBAL_IP, limit=LIMIT, window_seconds=60))
    monkeypatch.setattr(global_rate_limit, "POLICY_USER_DEFAULT",
                        RateLimitPolicy(name=f"t_user_{run}", scope=RateLimitScope.USER, limit=LIMIT, window_seconds=60),
                        raising=False)
    yield
    limiter.reset_rate_limit_backend()


def _from(client: TestClient, address: str) -> TestClient:
    return TestClient(client.app, client=(address, 40000))


def _hits(client: TestClient, headers: dict[str, str], n: int) -> list[int]:
    return [client.get("/api/v1/me/profile", headers=headers).status_code for _ in range(n)]


def test_colleagues_behind_one_office_address_each_have_their_own_allowance(
    client: TestClient, tenant: Fixture, tight_limits
) -> None:
    office = _from(client, "203.0.113.10")
    assert _hits(office, tenant.owner.headers, LIMIT) == [200] * LIMIT
    # Before: the colleague was refused, because the address had used the allowance up.
    assert _hits(office, tenant.contributor.headers, LIMIT) == [200] * LIMIT
    assert _hits(office, tenant.owner.headers, 1) == [429]


def test_one_person_on_many_addresses_shares_one_allowance(
    client: TestClient, tenant: Fixture, tight_limits
) -> None:
    statuses = []
    for n in range(LIMIT + 1):
        statuses += _hits(_from(client, f"198.51.100.{n + 1}"), tenant.viewer.headers, 1)
    assert statuses[-1] == 429, statuses


def test_anonymous_requests_are_still_counted_per_address(client: TestClient, tight_limits) -> None:
    anonymous = _from(client, "192.0.2.50")
    statuses = [anonymous.get("/api/v1/me/profile").status_code for _ in range(LIMIT + 1)]
    assert statuses[-1] == 429, statuses
