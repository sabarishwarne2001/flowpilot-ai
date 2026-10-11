"""A provider error must not strand, overcharge or needlessly fail a streamed answer (campaign session 2).

    pytest tests/engines/test_assistant_stream_provider_errors_live.py -q

Found live with the model stand-in told to answer one request with HTTP 503
(docs/hardening/tools/live_metering_probe.py):

* F-257. A provider that refused the request before any token left the assistant's answer
  stuck in STREAMING forever and logged two ERROR tracebacks: settlement charged the
  reservation's ESTIMATED input tokens for a call the provider never served, then crashed on
  the zero-token output row ("a zero cost basis must be declared as ZERO_BYOK"), rolled back
  and never closed the message.
* F-258. One transient provider error (503, 502, a timeout) before the first token failed the
  streamed answer outright, while the plain (non-streamed) answer retries the same error.
* F-259. Nothing ever closed an answer stranded in STREAMING (a crashed settlement, a process
  that died mid-stream): `stream_session.sweep_in_flight` found them and had no caller.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.models.assistant import ConversationMessage, StreamState
from app.models.usage_event import UsageEvent
from app.schemas.assistant import TokenUsage
from app.services import llm_stream
from app.services.byok.provider_clients import SOURCE_PLATFORM
from tests.engines.conftest import Engines


class _ProviderUnavailable(Exception):
    status_code = 503


def _opened(text: str) -> llm_stream.StreamSession:
    from app.services.byok.provider_clients import CredentialUse

    def chunks():
        yield llm_stream.StreamChunk(text=text)
        yield llm_stream.StreamChunk(
            usage=TokenUsage(provider="groq", model="openai/gpt-oss-20b", prompt_tokens=200,
                             completion_tokens=12, total_tokens=212, estimated_cost=0.0),
            finish_reason="stop",
        )

    return llm_stream.StreamSession(
        chunks=chunks(),
        credential_use=CredentialUse(source=SOURCE_PLATFORM, provider="GROQ", organization_id=uuid.uuid4(), reason="test"),
    )


def _ask(engines: Engines, question: str = "What is the total amount of the invoice?") -> tuple[str, uuid.UUID]:
    from app import crud
    from app.services.workspace_service import add_default_settings

    if crud.get_ai_settings(engines.db, workspace_id=engines.ws) is None:
        add_default_settings(engines.db, workspace_id=engines.ws, actor_id=engines.tenant.owner.user.id)
        engines.db.commit()
    created = engines.post("/assistant/conversations", {"title": "Provider errors"})
    assert created.status_code in (200, 201), created.text
    conversation_id = created.json()["id"]
    response = engines.client.post(
        engines.url(f"/assistant/conversations/{conversation_id}/messages/stream"),
        json={"content": question},
        headers=engines.tenant.owner.headers,
    )
    assert response.status_code == 200, response.text
    return response.text, uuid.UUID(conversation_id)


def _assistant_messages(engines: Engines, conversation_id: uuid.UUID) -> list[ConversationMessage]:
    engines.refresh()
    return list(engines.db.execute(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id, ConversationMessage.role == "assistant"
        )
    ).scalars())


def _llm_rows(engines: Engines) -> list[UsageEvent]:
    engines.refresh()
    return list(engines.db.execute(
        select(UsageEvent).where(
            UsageEvent.organization_id == engines.org, UsageEvent.event_type.like("llm.%")
        )
    ).scalars())


def test_a_refused_request_is_not_charged_and_the_answer_is_closed(engines: Engines, monkeypatch) -> None:
    def refuse(*args, **kwargs):
        raise _ProviderUnavailable("Error code: 503 - service unavailable")

    monkeypatch.setattr(llm_stream, "open_stream", refuse)
    body, conversation_id = _ask(engines)

    assert "event: error" in body
    [answer] = _assistant_messages(engines, conversation_id)
    assert answer.stream_state is StreamState.ABORTED, f"the answer is stuck in {answer.stream_state}"
    assert answer.finish_reason == "provider_error"
    assert _llm_rows(engines) == [], "a call the provider refused was charged"


def test_one_transient_provider_error_before_the_first_token_is_retried(engines: Engines, monkeypatch) -> None:
    calls = {"count": 0}

    def flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise _ProviderUnavailable("Error code: 503 - service unavailable")
        return _opened("The total is 1,234.50 USD.")

    monkeypatch.setattr(llm_stream, "open_stream", flaky)
    body, conversation_id = _ask(engines)

    assert calls["count"] == 2, "a transient error before the first token was not retried"
    assert "event: error" not in body
    assert "1,234.50" in body
    [answer] = _assistant_messages(engines, conversation_id)
    assert answer.stream_state is StreamState.COMPLETE
    rows = _llm_rows(engines)
    assert sorted(row.event_type for row in rows) == ["llm.input_token", "llm.output_token"], "charged once"


def test_a_permanent_provider_error_is_not_retried(engines: Engines, monkeypatch) -> None:
    calls = {"count": 0}

    class _BadRequest(Exception):
        status_code = 400

    def bad(*args, **kwargs):
        calls["count"] += 1
        raise _BadRequest("Error code: 400 - invalid model")

    monkeypatch.setattr(llm_stream, "open_stream", bad)
    body, _ = _ask(engines)
    assert calls["count"] == 1
    assert "event: error" in body


def test_an_answer_with_no_output_tokens_settles(engines: Engines, monkeypatch) -> None:
    from app.services.byok.provider_clients import CredentialUse

    def empty(*args, **kwargs):
        def chunks():
            yield llm_stream.StreamChunk(
                usage=TokenUsage(provider="groq", model="openai/gpt-oss-20b", prompt_tokens=180,
                                 completion_tokens=0, total_tokens=180, estimated_cost=0.0),
                finish_reason="stop",
            )

        return llm_stream.StreamSession(
            chunks=chunks(),
            credential_use=CredentialUse(source=SOURCE_PLATFORM, provider="GROQ", organization_id=uuid.uuid4(), reason="t"),
        )

    monkeypatch.setattr(llm_stream, "open_stream", empty)
    _, conversation_id = _ask(engines)
    [answer] = _assistant_messages(engines, conversation_id)
    assert answer.stream_state is StreamState.COMPLETE, f"the answer is stuck in {answer.stream_state}"
    assert [row.event_type for row in _llm_rows(engines)] == ["llm.input_token"], "the prompt is charged, nothing else"


def test_a_stranded_streaming_answer_is_closed_by_the_sweep(engines: Engines, monkeypatch) -> None:
    from app.workers.dead_letter import handle_pipeline_sweep_stuck

    monkeypatch.setattr(llm_stream, "open_stream", lambda *a, **k: _opened("Fine."))
    _, conversation_id = _ask(engines)
    [answer] = _assistant_messages(engines, conversation_id)
    # As a crashed settlement or a dead process leaves it.
    engines.db.execute(
        update(ConversationMessage).where(ConversationMessage.id == answer.id).values(
            stream_state=StreamState.STREAMING, finish_reason=None,
            stream_started_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    )
    engines.db.commit()

    handle_pipeline_sweep_stuck({})

    [answer] = _assistant_messages(engines, conversation_id)
    assert answer.stream_state is StreamState.ABORTED, "a stranded answer is never closed"
    assert answer.finish_reason == "deadline_exceeded"
