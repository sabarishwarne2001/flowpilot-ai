"""The internal reranker service itself: its probes, its token and its scoring (campaign session 4).

    pytest tests/services/test_reranker_service.py -q

Only the API's client of the reranker was tested (it degrades to the first-stage order when the
service is slow or wrong). The service (`app/reranker/main.py`, the `reranker` container on port
8081) is driven here through its own routes: liveness, readiness before and after the model loads,
the internal token, the caps on what it reads, and the scores it returns. The cross-encoder model
is stood in (a word-overlap scorer): the code under test is the service around it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.reranker import main as reranker

pytestmark = pytest.mark.no_db

TOKEN = "internal-test-token-0123456789"


class _Overlap:
    """Scores a passage by how many of the query's words it contains."""

    def __init__(self) -> None:
        self.calls: list[list[tuple[str, str]]] = []

    def predict(self, pairs):  # noqa: ANN001 - the CrossEncoder signature
        self.calls.append(list(pairs))
        return [float(len(set(q.lower().split()) & set(t.lower().split()))) for q, t in pairs]


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(reranker, "PRELOAD", False)
    monkeypatch.setattr(reranker, "INTERNAL_TOKEN", TOKEN)
    holder = reranker._ModelHolder()  # noqa: SLF001 - a fresh, unloaded model for each test
    monkeypatch.setattr(reranker, "holder", holder)
    with TestClient(reranker.app) as client:
        yield client, holder


def _load(holder, model) -> None:  # noqa: ANN001
    holder._model = model  # noqa: SLF001 - what a successful load leaves behind


def _rerank(client: TestClient, body: dict, token: str = TOKEN):
    return client.post("/rerank", json=body, headers={"X-Internal-Token": token})


def test_liveness_and_readiness(service) -> None:
    client, holder = service
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").status_code == 503  # the model has not loaded yet
    _load(holder, _Overlap())
    assert client.get("/ready").json() == {"status": "ready", "model": reranker.MODEL_NAME}


def test_scores_come_back_for_each_passage_in_order(service) -> None:
    client, holder = service
    model = _Overlap()
    _load(holder, model)
    answer = _rerank(client, {"query": "late delivery penalty", "passages": [
        {"id": "a", "text": "Payment terms are net 30"},
        {"id": "b", "text": "A late delivery incurs a penalty of 2%"},
    ]})
    assert answer.status_code == 200, answer.text
    scores = {s["id"]: s["score"] for s in answer.json()["scores"]}
    assert [s["id"] for s in answer.json()["scores"]] == ["a", "b"]
    assert scores["b"] > scores["a"]
    assert answer.json()["truncated"] is False


def test_the_internal_token_is_required(service, monkeypatch: pytest.MonkeyPatch) -> None:
    client, holder = service
    _load(holder, _Overlap())
    body = {"query": "q", "passages": [{"id": "a", "text": "t"}]}
    assert _rerank(client, body, token="wrong").status_code == 401
    monkeypatch.setattr(reranker, "INTERNAL_TOKEN", "")
    assert _rerank(client, body).status_code == 500  # misconfigured: refuses everyone


def test_what_it_reads_is_capped(service) -> None:
    client, holder = service
    model = _Overlap()
    _load(holder, model)
    passages = [{"id": str(n), "text": "x" * (reranker.MAX_TEXT_CHARS + 50)} for n in range(reranker.MAX_PAIRS + 5)]
    answer = _rerank(client, {"query": "q" * (reranker.MAX_QUERY_CHARS + 10), "passages": passages})
    assert answer.status_code == 200, answer.text
    assert len(answer.json()["scores"]) == reranker.MAX_PAIRS
    assert answer.json()["truncated"] is True
    [pairs] = model.calls
    assert len(pairs[0][0]) == reranker.MAX_QUERY_CHARS
    assert all(len(text) == reranker.MAX_TEXT_CHARS for _, text in pairs)
    assert _rerank(client, {"query": "q", "passages": []}).status_code == 422


def test_exactly_the_maximum_is_not_reported_as_truncated(service) -> None:
    # Campaign session 4 (F-349): `truncated` was "at least MAX_PAIRS passages after capping", so a
    # request of exactly MAX_PAIRS passages, none dropped, was reported as truncated.
    client, holder = service
    _load(holder, _Overlap())
    passages = [{"id": str(n), "text": "t"} for n in range(reranker.MAX_PAIRS)]
    answer = _rerank(client, {"query": "q", "passages": passages})
    assert answer.json()["truncated"] is False


def test_a_model_that_cannot_load_is_a_503_not_a_crash(service, monkeypatch: pytest.MonkeyPatch) -> None:
    client, holder = service

    def fail() -> None:
        holder._error = "OSError: no network"  # noqa: SLF001
        raise OSError("no network")

    monkeypatch.setattr(holder, "get", fail)
    answer = _rerank(client, {"query": "q", "passages": [{"id": "a", "text": "t"}]})
    assert answer.status_code == 503
    assert "model unavailable" in answer.text
