"""The workspace's temperature is the one the assistant's answers are written at (campaign session 4).

    pytest tests/engines/test_assistant_temperature_reaches_the_model_live.py -q

`Settings > AI > Temperature` says it shapes Ask AI answers only (F-307: document reading runs at
fixed, low temperatures). Until now only the wording and the reading side were proven. Here an
admin changes the temperature through the endpoint the page calls, then asks a question, both as a
streamed answer (what the product's Ask AI uses) and as a plain one (the non-streamed endpoint):
the request that reaches the provider carries the temperature just saved, and a second change is
followed by the next answer. The provider is the platform's Groq client, replaced by a recording
stand-in; everything between the API and that client (settings, routing, metering, the stream
adapter) runs for real.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from tests.engines.conftest import Engines, drain, make_pdf

ANSWER = "The total of the safety gloves invoice is 1,234.50 USD, as printed on the document."


class _RecordingGroq:
    """The provider: records each request and answers it, streamed or whole, in Groq's response shape."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        usage = SimpleNamespace(prompt_tokens=120, completion_tokens=18, total_tokens=138)
        if kwargs.get("stream"):
            def chunks():
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=ANSWER),
                                                               finish_reason=None)], usage=None, x_groq=None)
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=""),
                                                               finish_reason="stop")],
                                      usage=None, x_groq=SimpleNamespace(usage=usage))
            return chunks()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=ANSWER))], usage=usage)


@pytest.fixture()
def provider(engines: Engines, monkeypatch: pytest.MonkeyPatch) -> _RecordingGroq:
    from app.services.byok.provider_clients import SOURCE_PLATFORM, CredentialUse, ProviderClientFactory
    from app.services.llm_service import llm_service
    from app.services.sovereign import local_llm

    stand_in = _RecordingGroq()
    monkeypatch.setattr(local_llm, "is_exclusive", lambda: False)
    # A plain answer goes to the platform's Groq client; a streamed one builds its client per request.
    monkeypatch.setattr(llm_service, "_groq_client", stand_in)

    def build(db, *, organization_id, provider, prefer_tenant_key=True):
        return stand_in, CredentialUse(source=SOURCE_PLATFORM, provider=provider.upper(),
                                       organization_id=organization_id, reason="recorded provider")

    monkeypatch.setattr(ProviderClientFactory, "build", staticmethod(build))
    return stand_in


def _save_temperature(engines: Engines, temperature: float) -> None:
    from app import crud
    from app.services.workspace_service import add_default_settings

    if crud.get_ai_settings(engines.db, workspace_id=engines.ws) is None:
        add_default_settings(engines.db, workspace_id=engines.ws, actor_id=engines.tenant.owner.user.id)
        engines.db.commit()
    current = engines.get("/ai-settings")
    assert current.status_code == 200, current.text
    body = {key: current.json()[key] for key in ("provider", "model", "max_output_tokens", "top_p",
                                                  "frequency_penalty", "presence_penalty", "enable_streaming")}
    saved = engines.put("/ai-settings", {**body, "provider": "GROQ", "model": "openai/gpt-oss-20b",
                                         "temperature": temperature})
    assert saved.status_code == 200, saved.text
    assert saved.json()["temperature"] == temperature


def _conversation(engines: Engines) -> str:
    created = engines.post("/assistant/conversations", {"title": "Temperature"})
    assert created.status_code in (200, 201), created.text
    return created.json()["id"]


def _ask_streamed(engines: Engines, conversation_id: str) -> str:
    response = engines.client.post(
        engines.url(f"/assistant/conversations/{conversation_id}/messages/stream"),
        json={"content": "What is the total of the safety gloves invoice?"},
        headers=engines.tenant.owner.headers,
    )
    assert response.status_code == 200, response.text
    assert "event: error" not in response.text, response.text[-600:]
    return response.text


def test_a_streamed_answer_is_written_at_the_saved_temperature(engines: Engines, provider: _RecordingGroq) -> None:
    _save_temperature(engines, 0.37)
    conversation_id = _conversation(engines)
    body = _ask_streamed(engines, conversation_id)
    assert "1,234.50" in body
    [request] = provider.requests
    assert request["stream"] is True
    assert request["temperature"] == 0.37, request

    _save_temperature(engines, 1.25)
    _ask_streamed(engines, conversation_id)
    assert [r["temperature"] for r in provider.requests] == [0.37, 1.25]


def test_a_plain_answer_is_written_at_the_saved_temperature(engines: Engines, provider: _RecordingGroq) -> None:
    # The plain endpoint asks the model only when the knowledge base has something to say, so a document
    # is indexed first (its text layer, chunked and embedded by the real pipeline).
    engines.upload("gloves.pdf", make_pdf([["TAX INVOICE", "Safety gloves invoice",
                                            "Total of the safety gloves invoice: 1,234.50 USD"]]))
    drain()
    _save_temperature(engines, 0.42)
    conversation_id = _conversation(engines)

    def ask() -> dict:
        response = engines.post(f"/assistant/conversations/{conversation_id}/messages",
                                {"content": "What is the total of the safety gloves invoice?"})
        assert response.status_code == 200, response.text
        return response.json()

    first = ask()
    assert provider.requests, f"the model was never asked: {json.dumps(first)[:400]}"
    assert provider.requests[-1].get("stream") in (None, False)
    assert provider.requests[-1]["temperature"] == 0.42, provider.requests[-1]

    _save_temperature(engines, 0.9)
    ask()
    assert [r["temperature"] for r in provider.requests] == [0.42, 0.9]
