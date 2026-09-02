"""ARCH-19 §3.3 — reranker degradation labelling and metrics.

The open-degradation behaviour itself is ARCH-11's and is already covered by
tests/services/test_reranker_degradation.py, which asserts the lowercase wire
reasons on each result. This module does not repeat those. It covers what
ARCH-19 adds: the operator-facing vocabulary, the counter, and the disabled
path that previously degraded without declaring it.
"""

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
    breaker_registry.pop(rc.BREAKER_NAME, None)
    rc.reset_degradation_metrics()

    monkeypatch.setattr(rc.settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(rc.settings, "RERANK_MIN_RESULTS", 2)
    monkeypatch.setattr(rc.settings, "RERANK_MAX_CANDIDATES", 15)
    monkeypatch.setattr(rc.settings, "RERANK_FINAL_RESULTS", 4)

    yield rc.RerankerClient()

    breaker_registry.pop(rc.BREAKER_NAME, None)
    rc.reset_degradation_metrics()


def _results(count: int = 6) -> list[dict]:
    return [
        {"id": f"chunk-{i}", "text": f"body {i}", "metadata": {}}
        for i in range(count)
    ]


def _stub(target, raiser=None, payload=None):
    class _Transport:
        def post_json(self, path, body, *, request_id=None):
            if raiser is not None:
                raise raiser
            return InternalResponse(status=200, payload=payload, elapsed_ms=1.0)

    target._client = _Transport()  # noqa: SLF001
    return target


# ---------------------------------------------------------------------------
# The roadmap's operator vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason,label",
    [
        (rc.REASON_DISABLED, "RERANKER_DISABLED"),
        (rc.REASON_TIMEOUT, "TIMEOUT"),
        (rc.REASON_BREAKER_OPEN, "CIRCUIT_OPEN"),
        (rc.REASON_UNAVAILABLE, "UNAVAILABLE"),
    ],
)
def test_roadmap_labels_are_exact(reason: str, label: str) -> None:
    """§3.3 names these four. They are asserted verbatim because an operator
    dashboard filters on the string."""
    assert rc.degraded_label(reason) == label


def test_every_wire_reason_has_a_label() -> None:
    for reason in rc.DEGRADE_REASONS:
        assert rc.degraded_label(reason) != "UNKNOWN", (
            f"{reason} degrades but has no operator label"
        )


def test_labels_are_a_closed_set() -> None:
    assert set(rc.DEGRADED_REASON_LABELS.values()) == set(
        rc.DEGRADED_REASON_LABEL_VALUES
    )
    assert len(rc.DEGRADED_REASON_LABELS) == len(rc.DEGRADE_REASONS)


def test_wire_vocabulary_stays_lowercase() -> None:
    """ARCH-11's suite and the ARCH-0G gate assert these exact strings.

    Renaming a payload vocabulary to make a log line prettier is not a trade
    worth making, so the uppercase names live only in logs and the counter.
    """
    for reason in rc.DEGRADE_REASONS:
        assert reason == reason.lower()


# ---------------------------------------------------------------------------
# The disabled path
# ---------------------------------------------------------------------------


def test_disabled_reranker_declares_a_reason(rerank_client, monkeypatch) -> None:
    """Previously the one degradation an operator caused on purpose was the
    only one invisible to the metric."""
    monkeypatch.setattr(rc.settings, "RERANKER_ENABLED", False)

    returned = rerank_client.rerank(query="q", results=_results())

    assert returned
    assert all(item["rerank_status"] == rc.STATUS_DISABLED for item in returned)
    assert all(
        item["rerank_degraded_reason"] == rc.REASON_DISABLED for item in returned
    )
    assert rc.degradation_metrics()["by_reason"]["RERANKER_DISABLED"] == 1


def test_skipped_is_not_counted_as_degradation(rerank_client) -> None:
    """Too few candidates for reranking to change the ordering is not an
    outage, and counting it as one would bury the real signal."""
    returned = rerank_client.rerank(query="q", results=_results(1))

    assert all(item["rerank_status"] == rc.STATUS_SKIPPED for item in returned)
    assert rc.degradation_metrics()["degraded"] == 0


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


def test_failures_increment_the_counter(rerank_client) -> None:
    _stub(rerank_client, raiser=InternalServiceTimeout("exceeded 2.0s"))
    rerank_client.rerank(query="q", results=_results())

    metrics = rc.degradation_metrics()
    assert metrics["by_reason"]["TIMEOUT"] == 1
    assert metrics["degraded"] == 1
    assert metrics["calls_total"] == 1
    assert metrics["degraded_ratio"] == 1.0


def test_success_and_failure_are_counted_separately(rerank_client) -> None:
    _stub(
        rerank_client,
        payload={"scores": [{"id": "chunk-0", "score": 0.9},
                            {"id": "chunk-1", "score": 0.1}]},
    )
    rerank_client.rerank(query="q", results=_results(3))

    _stub(rerank_client, raiser=InternalServiceError("connection refused"))
    rerank_client.rerank(query="q", results=_results(3))

    metrics = rc.degradation_metrics()
    assert metrics["calls_total"] == 2
    assert metrics["reranked"] == 1
    assert metrics["degraded"] == 1
    assert metrics["degraded_ratio"] == 0.5
    assert metrics["by_reason"] == {"UNAVAILABLE": 1}


def test_health_exposes_the_counter(rerank_client) -> None:
    _stub(rerank_client, raiser=InternalServiceError("down"))
    rerank_client.rerank(query="q", results=_results())

    health = rerank_client.health()
    assert "degradation" in health
    assert health["degradation"]["by_reason"]["UNAVAILABLE"] == 1


def test_metrics_reset_is_total(rerank_client) -> None:
    _stub(rerank_client, raiser=InternalServiceTimeout("t"))
    rerank_client.rerank(query="q", results=_results())

    rc.reset_degradation_metrics()

    metrics = rc.degradation_metrics()
    assert metrics == {
        "calls_total": 0,
        "reranked": 0,
        "degraded": 0,
        "degraded_ratio": 0.0,
        "by_reason": {},
    }


# ---------------------------------------------------------------------------
# The invariant that matters more than any label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raiser",
    [
        InternalServiceTimeout("budget exceeded"),
        InternalServiceError("reranker unreachable: connection refused"),
        ValueError("malformed RERANKER_URL"),
        RuntimeError("something nobody anticipated"),
    ],
)
def test_no_failure_mode_reaches_the_caller(rerank_client, raiser) -> None:
    """A reranker outage costs relevance, never availability.

    Parametrised over an unanticipated exception type on purpose: the bare
    `except Exception` arm is the one that keeps a 500 out of the assistant
    when the sidecar fails in a way nobody predicted.
    """
    _stub(rerank_client, raiser=raiser)

    returned = rerank_client.rerank(query="q", results=_results())

    assert returned, "degrading must still return the RRF order, not nothing"
    assert len(returned) == rc.settings.RERANK_FINAL_RESULTS
    assert all(item["rerank_status"] == rc.STATUS_DEGRADED for item in returned)
    assert all(
        item["rerank_degraded_reason"] in rc.DEGRADE_REASONS for item in returned
    )


def test_degraded_order_is_the_rrf_order(rerank_client) -> None:
    """Degrading must preserve the fusion ranking, not shuffle it."""
    _stub(rerank_client, raiser=InternalServiceError("down"))
    original = _results()

    returned = rerank_client.rerank(query="q", results=list(original))

    assert [item["id"] for item in returned] == [
        item["id"] for item in original[: rc.settings.RERANK_FINAL_RESULTS]
    ]
