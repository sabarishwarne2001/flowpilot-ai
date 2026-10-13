"""A workspace's AI settings reach the provider request, as the settings page says (campaign session 3).

`Settings > AI` stores a model, an output limit, top-p and penalties per workspace, plus a
temperature that (since F-307) the page says shapes the assistant's answers only: document
reading runs at fixed, low temperatures so the same document reads the same way twice. Nothing
showed any of it reaching a provider; the engine tests replace the model functions whole. Here
the platform's Groq client is a recording stand-in and a document is classified, its fields
extracted and summarised with one workspace's settings: each request carries that workspace's
model, output limit, top-p and penalties, and the fixed reading temperatures, not the
assistant temperature.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import settings
from app.models.ai_settings import AISettings
from app.schemas.ai_settings import AIProvider
from app.services.llm_service import LLMService

pytestmark = pytest.mark.no_db


class _Groq:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        content = json.dumps({"document_classification": "Invoice", "confidence": 0.9, "invoice_number": "INV-1"})
        if "summar" in kwargs["messages"][0]["content"].lower():
            content = "A short summary."
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20),
        )


@pytest.fixture
def workspace_settings() -> AISettings:
    return AISettings(provider=AIProvider.GROQ, model="openai/gpt-oss-20b", temperature=0.73, max_output_tokens=777,
                      top_p=0.85, frequency_penalty=0.2, presence_penalty=0.1)


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> tuple[LLMService, _Groq]:
    from app.services.sovereign import local_llm

    monkeypatch.setattr(local_llm, "is_exclusive", lambda: False)
    stand_in = _Groq()
    instance = LLMService()
    instance._groq_client = stand_in  # noqa: SLF001 - the platform client the workspace's calls go to
    return instance, stand_in


def test_each_document_call_carries_the_workspaces_model_and_limits(service, workspace_settings: AISettings) -> None:
    instance, groq = service
    text = "ACME SUPPLIES LTD\nTAX INVOICE\nInvoice No: INV-1\nTotal: 120.00"
    instance.classify_document(text, ai_settings=workspace_settings)
    instance.extract_entities(text, "Invoice", ai_settings=workspace_settings)
    instance.generate_summary(text, ai_settings=workspace_settings)

    assert len(groq.requests) == 3, groq.requests
    for request in groq.requests:
        assert request["model"] == "openai/gpt-oss-20b"
        assert request["max_tokens"] == 777
        assert (request["top_p"], request["frequency_penalty"], request["presence_penalty"]) == (0.85, 0.2, 0.1)
    temperatures = [request["temperature"] for request in groq.requests]
    assert temperatures == [settings.LLM_CLASSIFICATION_TEMPERATURE, settings.LLM_ENTITY_EXTRACTION_TEMPERATURE,
                            settings.LLM_SUMMARIZATION_TEMPERATURE]
    assert 0.73 not in temperatures, "document reading does not take the assistant's temperature (F-307)"
