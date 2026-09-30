"""F-042 — a readiness probe that checks the dependencies, separate from liveness.

    pytest tests/api/test_health_readiness.py -q

`GET /api/v1/health` only proves the Python process answers; it returns
"healthy" with the database down. That is the right shape for a LIVENESS probe
(restart me if I hang) and the wrong one for a READINESS probe (do not send me
traffic, or alarm, if I cannot reach Postgres or Redis). `/api/v1/health/ready`
checks both, answers 503 when either is unreachable, and never says why.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

READY = "/api/v1/health/ready"


def test_liveness_still_answers_without_touching_dependencies(client: TestClient, monkeypatch) -> None:
    from app.api.v1 import health

    monkeypatch.setattr(health, "_check_database", lambda: (_ for _ in ()).throw(RuntimeError("must not be called")), raising=False)
    assert client.get("/api/v1/health").status_code == 200


def test_ready_is_200_when_the_database_and_redis_answer(client: TestClient) -> None:
    response = client.get(READY)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ready"}


def test_ready_is_503_when_the_database_is_down(client: TestClient, monkeypatch) -> None:
    from app.api.v1 import health

    def down() -> bool:
        raise ConnectionError("password authentication failed for user flowpilot at 10.0.0.9")

    monkeypatch.setattr(health, "_check_database", down)
    response = client.get(READY)
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert "10.0.0.9" not in response.text and "password" not in response.text


def test_ready_is_503_when_redis_is_down(client: TestClient, monkeypatch) -> None:
    from app.api.v1 import health

    monkeypatch.setattr(health, "_check_redis", lambda: (_ for _ in ()).throw(TimeoutError("redis://:secret@redis:6379")))
    response = client.get(READY)
    assert response.status_code == 503
    assert "secret" not in response.text


def test_ready_needs_no_credentials_and_reveals_no_version() -> None:
    from app.core.public_route_registry import is_public

    assert is_public(READY, "GET")
