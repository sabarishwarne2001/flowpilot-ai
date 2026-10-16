"""From a sentence to a mandate draft, through the real API (brief §5.3).

The model is replaced at its lowest layer (`llm_service._execute_query`), so the routing, the
metering reservation and its settlement all run for real: the tests see the usage rows a drafting
call writes, the 402 a spend ceiling returns before any provider is called, and the gallery a bad
or missing model falls back to.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable

import pytest
from sqlalchemy import select

from app.models.helm import HelmCounterparty, HelmMandate
from app.models.usage_event import UsageEvent
from app.schemas.assistant import TokenUsage
from tests.engines.conftest import Engines

SENTENCE = "Keep our cash above 5 lakh rupees"


class _Model:
    """Answers the drafting prompt with whatever the test says, and remembers the prompts."""

    def __init__(self, answer: Callable[[str], str]) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def __call__(self, *, prompt: str, **_: Any) -> tuple[str, TokenUsage]:
        self.prompts.append(prompt)
        return self.answer(prompt), TokenUsage(provider="groq", model="openai/gpt-oss-20b", prompt_tokens=900,
                                               completion_tokens=40, total_tokens=940, estimated_cost=0.0)


def _model(monkeypatch, answer: Callable[[str], str] | dict) -> _Model:
    from app.services.llm_service import llm_service

    fake = _Model(answer if callable(answer) else (lambda _p: json.dumps(answer)))
    monkeypatch.setattr(llm_service, "_execute_query", fake)
    return fake


def _book(engines: Engines, *, settings: bool = True) -> str:
    from app import crud
    from app.services.workspace_service import add_default_settings

    engines.plan("business")
    if settings and crud.get_ai_settings(engines.db, workspace_id=engines.ws) is None:
        add_default_settings(engines.db, workspace_id=engines.ws, actor_id=engines.tenant.owner.user.id)
    engines.db.commit()
    created = engines.client.post(engines.org_url("/helm/books"), json={"workspace_id": str(engines.ws)},
                                  headers=engines.tenant.owner.headers)
    assert created.status_code in (200, 201), created.text
    return created.json()["id"]


def _draft(engines: Engines, book_id: str, sentence: str = SENTENCE, *, persona=None):
    return engines.client.post(engines.org_url(f"/helm/books/{book_id}/mandates/draft"), json={"sentence": sentence},
                               headers=(persona or engines.tenant.owner).headers)


def _llm_rows(engines: Engines) -> list[UsageEvent]:
    engines.db.expire_all()
    return list(engines.db.execute(select(UsageEvent).where(
        UsageEvent.organization_id == engines.org, UsageEvent.event_type.like("llm.%"))).scalars())


def test_a_sentence_becomes_a_checked_draft_and_the_call_is_metered(engines: Engines, monkeypatch) -> None:
    book_id = _book(engines)
    model = _model(monkeypatch, {"template": "keep_cash_above",
                                 "params": {"floor": {"amount": "500000", "currency": "inr"}},
                                 "counterparties": [], "alternatives": ["pay_on_time", "no_such_mandate"]})
    response = _draft(engines, book_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "model" and body["template"]["template"] == "keep_cash_above"
    draft = body["draft"]
    assert draft["valid"] is True, draft["problems"]
    assert draft["spec"]["params"]["floor"] == {"amount_micros": 500_000 * 1_000_000, "currency": "INR"}
    # A draft never sets autonomy: like every new mandate it starts in Propose mode, with no act cap.
    assert draft["spec"]["autonomy"] == {"mode": "propose", "act_cap": None}
    assert draft["readback"]["sentence"].startswith("Keep INR cash above ₹5 lakh")
    # "Did you mean": only library keys, never the chosen one or one the model made up.
    assert [a["template"] for a in body["alternatives"]][:1] == ["pay_on_time"]
    assert "no_such_mandate" not in [a["template"] for a in body["alternatives"]]
    assert body["backtest"] == {"months": 6}
    # The prompt lists the library and keeps the sentence as data.
    prompt = model.prompts[-1]
    assert prompt.startswith("You are Helm's mandate drafter.") and "keep_cash_above" in prompt
    assert f"<<<{SENTENCE}>>>" in prompt
    # Nothing is saved; the call is metered like any assistant call.
    assert engines.db.execute(select(HelmMandate).where(HelmMandate.book_id == uuid.UUID(book_id))).first() is None
    assert sorted(row.event_type for row in _llm_rows(engines)) == ["llm.input_token", "llm.output_token"]


def test_an_amount_the_sentence_does_not_state_is_never_shown_as_a_fact(engines: Engines, monkeypatch) -> None:
    book_id = _book(engines)
    _model(monkeypatch, {"template": "keep_cash_above", "params": {}})
    body = _draft(engines, book_id, "Never let our cash run low").json()
    assert body["source"] == "model"
    assert body["draft"]["valid"] is False and body["draft"]["unstated"] == ["floor"]
    assert any("does not say the floor" in p for p in body["draft"]["problems"])
    assert body["backtest"] is None


def test_a_supplier_the_book_has_never_seen_is_named_not_guessed(engines: Engines, monkeypatch) -> None:
    book_id = _book(engines)
    engines.db.add(HelmCounterparty(organization_id=engines.org, book_id=uuid.UUID(book_id), key="name:konkan",
                                    role="SUPPLIER", display_name="Konkan Packaging Pvt Ltd"))
    engines.db.commit()
    _model(monkeypatch, {"template": "never_pay_duplicate", "params": {},
                         "counterparties": ["konkan packaging", "Zenith Steel"]})
    body = _draft(engines, book_id, "Never pay Konkan Packaging or Zenith Steel twice").json()
    assert body["draft"]["spec"]["scope"]["counterparty_keys"] == ["name:konkan"]
    assert body["unknown_counterparties"] == ["Zenith Steel"]
    assert body["draft"]["valid"] is False
    assert body["draft"]["problems"][0] == 'Helm has not seen a supplier or customer called "Zenith Steel" in this book.'


@pytest.mark.parametrize("answer, reason", [
    ("Sure! I would suggest keeping a cash floor.", "NOT_UNDERSTOOD"),
    ('{"template": "keep_cash_above", "params": {}, "autonomy": {"mode": "act"}}', "NOT_UNDERSTOOD"),
    ('{"template": "wire_money_abroad", "params": {}}', "NOT_UNDERSTOOD"),
    ('{"template": null, "params": {}}', "NO_TEMPLATE"),
])
def test_a_confused_model_falls_back_to_the_gallery(engines: Engines, monkeypatch, answer, reason) -> None:
    book_id = _book(engines)
    _model(monkeypatch, lambda _p: answer)
    response = _draft(engines, book_id, "Keep cash above a floor every month")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "gallery" and body["reason"] == reason and body["draft"] is None
    assert body["alternatives"][0]["template"] == "keep_cash_above"


def test_an_unreachable_or_missing_model_falls_back_to_the_gallery(engines: Engines, monkeypatch) -> None:
    book_id = _book(engines)

    def down(_prompt: str) -> str:
        raise TimeoutError("provider timed out")

    _model(monkeypatch, down)
    body = _draft(engines, book_id, "Never pay a duplicate invoice").json()
    assert (body["source"], body["reason"]) == ("gallery", "MODEL_UNAVAILABLE")
    assert body["alternatives"][0]["template"] == "never_pay_duplicate"

    from app.models.ai_settings import AISettings

    engines.db.query(AISettings).filter(AISettings.workspace_id == engines.ws).delete()
    engines.db.commit()
    body = _draft(engines, book_id, "Never pay a duplicate invoice").json()
    assert (body["source"], body["reason"]) == ("gallery", "NO_MODEL")


def test_a_spend_ceiling_refuses_drafting_before_any_provider_is_called(engines: Engines, monkeypatch) -> None:
    from app.core.exceptions import SpendLimitExceededError
    from app.services import spend_control_service

    book_id = _book(engines)
    model = _model(monkeypatch, {"template": "never_pay_duplicate"})

    def ceiling(*_a, **_k):
        raise SpendLimitExceededError(limit_key="llm.input_token", period="month", dimension="quantity",
                                      ceiling="1000", current="1000", requested="900")

    monkeypatch.setattr(spend_control_service, "ensure_within_limits", ceiling)
    response = _draft(engines, book_id)
    assert response.status_code == 402, response.text
    assert response.json()["code"] == "AI_LIMIT_REACHED"
    assert model.prompts == [], "the provider is never called past the ceiling"
    assert _llm_rows(engines) == []


def test_only_owners_and_admins_draft_mandates(engines: Engines, monkeypatch) -> None:
    book_id = _book(engines)
    model = _model(monkeypatch, {"template": "never_pay_duplicate"})
    assert _draft(engines, book_id, persona=engines.tenant.org_admin).status_code == 200
    refused = _draft(engines, book_id, persona=engines.tenant.contributor)
    assert refused.status_code == 403, refused.text
    assert len(model.prompts) == 1


def test_an_empty_or_long_sentence_is_refused_in_words(engines: Engines, monkeypatch) -> None:
    book_id = _book(engines)
    _model(monkeypatch, {"template": "never_pay_duplicate"})
    assert _draft(engines, book_id, "   ").status_code == 422
    assert _draft(engines, book_id, "x" * 501).status_code == 422
