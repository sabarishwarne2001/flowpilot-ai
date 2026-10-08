"""A new workspace runs a model the platform supports and prices (F-196).

Every workspace created through the product (a new organization's first workspace, or one added
under Workspaces) was given `mixtral-8x7b-32768` as its AI model, and that model is what every
extraction and assistant call sent to Groq. Groq retired it in March 2025 (and the older Llama
3.x models on 16 August 2026); it is also absent from the platform's own model list
(app/core/ai_models.py) and from the price book, so in production the first document in any
new workspace would have failed at the provider. The registry, the price book, the settings
page and the development reset had all moved to `openai/gpt-oss-*`; this path had not.
"""

from __future__ import annotations

from app.core.ai_models import AI_MODELS
from app.core.byok_providers import PROVIDER_REGISTRY, PROVIDER_GROQ
from app.schemas.ai_settings import AIProvider
from tests.engines.conftest import Engines

RETIRED_AT_GROQ = {"mixtral-8x7b-32768", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"}


def test_a_workspace_created_in_the_product_runs_a_listed_model(engines: Engines) -> None:
    created = engines.client.post(
        f"/api/v1/organizations/{engines.org}/workspaces",
        json={"workspace_name": "Receivables F-196"},
        headers=engines.tenant.owner.headers,
    )
    assert created.status_code == 201, created.text
    workspace_id = created.json()["id"]

    settings = engines.client.get(
        f"/api/v1/workspaces/{workspace_id}/ai-settings", headers=engines.tenant.owner.headers
    )
    assert settings.status_code == 200, settings.text
    body = settings.json()
    provider = AIProvider(body["provider"])
    assert body["model"] in AI_MODELS[provider], (
        f"a new workspace runs {body['model']!r}, which the platform does not list for {provider.value}"
    )
    assert body["model"] not in RETIRED_AT_GROQ


def test_byok_does_not_suggest_models_groq_has_retired() -> None:
    suggested = set(PROVIDER_REGISTRY[PROVIDER_GROQ].suggested_models)
    assert not suggested & RETIRED_AT_GROQ, sorted(suggested & RETIRED_AT_GROQ)
