"""ARCH-0G §4.4 — the reranker degrades open, including when it answers wrong."""

from __future__ import annotations

import pytest

from app.core.breaker import _REGISTRY as breaker_registry
from app.core.internal_http import (
    InternalResponse,
    InternalServiceError,
    InternalServiceTimeout,
)
from app.services import reranker_client as rc

pytestmark = pytest.mark.no_db


@pytest.fixture()
def rerank_client(monkeypatch):
    """A reranker client whose breaker starts closed."""
    breaker_registry.pop(rc.BREAKER_NAME, None)

    monkeypatch.setattr(rc.settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(rc.settings, "RERANK_MIN_RESULTS", 2)
    monkeypatch.setattr(rc.settings, "RERANK_MAX_CANDIDATES", 15)
    monkeypatch.setattr(rc.settings, "RERANK_FINAL_RESULTS", 4)

    yield rc.RerankerClient()

    breaker_registry.pop(rc.BREAKER_NAME, None)


def _results(count: int = 6) -> list[dict]:
    return [
        {"id": f"chunk-{index}", "text": f"body {index}", "metadata": {}}
        for index in range(count)
    ]


def _stub(client, raiser=None, payload=None):
    """Replace the transport, leaving the breaker and the parsing intact."""

    class _Transport:
        def post_json(self, path, body, *, request_id=None):
            if raiser is not None:
                raise raiser
            return InternalResponse(status=200, payload=payload, elapsed_ms=1.0)

    client._client = _Transport()  # noqa: SLF001
    return client


# ---------------------------------------------------------------------------
# Failure modes that were already handled
# ---------------------------------------------------------------------------


def test_unreachable_service_returns_rrf_order(rerank_client):
    _stub(rerank_client, raiser=InternalServiceError("reranker unreachable: refused"))
    original = _results()

    returned = rerank_client.rerank(query="q", results=list(original))

    assert [item["id"] for item in returned] == [
        item["id"] for item in original[: rc.settings.RERANK_FINAL_RESULTS]
    ]
    assert all(item["rerank_status"] == rc.STATUS_DEGRADED for item in returned)
    assert all(item["rerank_degraded_reason"] == "unavailable" for item in returned)


def test_timeout_returns_rrf_order(rerank_client):
    _stub(rerank_client, raiser=InternalServiceTimeout("exceeded 2.0s"))
    returned = rerank_client.rerank(query="q", results=_results())
    assert returned
    assert all(item["rerank_degraded_reason"] == "timeout" for item in returned)


# ---------------------------------------------------------------------------
# The gap ARCH-0G closes: it answered, and it was not the reranker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"error": "no healthy upstream"}, "malformed_response"),
        ([{"id": "chunk-0", "score": 1.0}], "malformed_response"),
        ({"scores": [{"identifier": "chunk-0", "value": 1.0}]}, "malformed_response"),
        ({"scores": [{"id": "chunk-0", "score": "very high"}]}, "malformed_response"),
        ({"scores": {"chunk-0": 1.0}}, "malformed_response"),
        ({"scores": []}, "empty_response"),
    ],
)
def test_malformed_response_degrades_instead_of_raising(rerank_client, payload, reason):
    _stub(rerank_client, payload=payload)

    returned = rerank_client.rerank(query="q", results=_results())

    assert returned, "degrading must still return the RRF order, not nothing"
    assert all(item["rerank_status"] == rc.STATUS_DEGRADED for item in returned)
    assert all(item["rerank_degraded_reason"] == reason for item in returned)


def test_unexpected_exception_degrades_instead_of_raising(rerank_client):
    _stub(rerank_client, raiser=ValueError("malformed RERANKER_URL"))

    returned = rerank_client.rerank(query="q", results=_results())

    assert returned
    assert all(
        item["rerank_degraded_reason"] == "unexpected_error" for item in returned
    )


def test_no_degrade_reason_is_undeclared(rerank_client):
    for payload in ({"error": "x"}, {"scores": []}):
        _stub(rerank_client, payload=payload)
        returned = rerank_client.rerank(query="q", results=_results())
        for item in returned:
            assert item["rerank_degraded_reason"] in rc.DEGRADE_REASONS


# ---------------------------------------------------------------------------
# The happy path still ranks
# ---------------------------------------------------------------------------


def test_valid_response_reorders_and_scores(rerank_client):
    _stub(
        rerank_client,
        payload={
            "scores": [
                {"id": "chunk-0", "score": 0.1},
                {"id": "chunk-1", "score": 0.9},
                {"id": "chunk-2", "score": 0.5},
            ]
        },
    )

    returned = rerank_client.rerank(query="q", results=_results(3))

    assert [item["id"] for item in returned] == ["chunk-1", "chunk-2", "chunk-0"]
    assert all(item["rerank_status"] == rc.STATUS_OK for item in returned)


def test_empty_input_is_returned_untouched(rerank_client):
    assert rerank_client.rerank(query="q", results=[]) == []
