"""The platform's own keys serve only the models the plans are priced for (campaign session 2, N-054, F-261).

    pytest tests/services/test_platform_served_models.py -q

A plan's token allowance is counted in tokens, and a token on Gemini 2.5 Pro costs the
platform 33 times one on GPT-OSS 20B. A workspace could select any listed model in
Settings -> AI and the platform's own key paid for it: at the ceilings an Enterprise seat on
Gemini 2.5 Pro cost $2,223 a month against $799. Premium models now run on the
organization's own key; on the platform's key a call is answered by the platform default.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.core.ai_models import PLATFORM_DEFAULT_MODEL, platform_safe, requires_own_key
from app.schemas.ai_settings import AIProvider
from app.schemas.assistant import TokenUsage


def _settings(provider: AIProvider, model: str) -> SimpleNamespace:
    return SimpleNamespace(provider=provider, model=model, temperature=0.2, max_output_tokens=512, top_p=1.0,
                           frequency_penalty=0.0, presence_penalty=0.0, enable_streaming=True)


@pytest.mark.parametrize(("provider", "model", "own_key"), [
    (AIProvider.GROQ, "openai/gpt-oss-20b", False),
    (AIProvider.GROQ, "openai/gpt-oss-120b", False),
    (AIProvider.GROQ, "qwen/qwen3.8-27b", True),
    (AIProvider.GEMINI, "gemini-2.5-pro", True),
    (AIProvider.GEMINI, "gemini-3.5-flash", True),
    (AIProvider.GROQ, "llama-3.3-70b-versatile", True),  # retired by Groq: never sent on our key
])
def test_which_models_need_the_organizations_own_key(provider, model, own_key) -> None:
    assert requires_own_key(provider, model) is own_key


def test_a_platform_call_with_a_premium_model_is_served_by_the_default(monkeypatch) -> None:
    import importlib

    module = importlib.import_module("app.services.llm_service")

    served: list[tuple[str, str]] = []

    def groq(service, *, prompt, temperature, ai_settings, client):
        served.append(("groq", ai_settings.model))
        return "ok", TokenUsage(provider="groq", model=ai_settings.model, prompt_tokens=10, completion_tokens=2,
                                total_tokens=12, estimated_cost=0.0)

    def gemini(*args, **kwargs):
        served.append(("gemini", kwargs["ai_settings"].model))
        raise AssertionError("the platform's Gemini key was used for a premium model")

    monkeypatch.setitem(module._COMPLETION_DISPATCH, "groq", groq)
    monkeypatch.setattr(module.llm_service, "_query_gemini", gemini)
    monkeypatch.setattr(module.llm_service, "_platform_client_for", lambda provider: object())

    text, usage = module.llm_service._execute_query(
        prompt="Hello", temperature=0.2, ai_settings=_settings(AIProvider.GEMINI, "gemini-2.5-pro"),
        organization_id=uuid.uuid4(),
    )

    assert served == [("groq", PLATFORM_DEFAULT_MODEL)]
    assert usage.model == PLATFORM_DEFAULT_MODEL


def test_the_organizations_own_key_serves_the_premium_model(monkeypatch) -> None:
    import importlib

    module = importlib.import_module("app.services.llm_service")

    served: list[str] = []

    def gemini(*, prompt, temperature, ai_settings, client):
        served.append(ai_settings.model)
        usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=2, total_token_count=12)
        return "ok", SimpleNamespace(usage_metadata=usage)

    monkeypatch.setattr(module.llm_service, "_query_gemini", gemini)
    module.llm_service._execute_query(
        prompt="Hello", temperature=0.2, ai_settings=_settings(AIProvider.GEMINI, "gemini-2.5-pro"),
        byok_client=object(), organization_id=uuid.uuid4(),
    )
    assert served == ["gemini-2.5-pro"]


def test_platform_safe_keeps_every_other_setting() -> None:
    base = _settings(AIProvider.GEMINI, "gemini-2.5-pro")
    safe = platform_safe(base)
    assert (safe.provider, safe.model) == (AIProvider.GROQ, PLATFORM_DEFAULT_MODEL)
    assert safe.temperature == 0.2 and safe.max_output_tokens == 512
    assert platform_safe(_settings(AIProvider.GROQ, "openai/gpt-oss-120b")).model == "openai/gpt-oss-120b"


def test_a_stream_on_the_platform_key_with_a_premium_model_is_served_by_the_default(monkeypatch) -> None:
    from app.services import llm_stream
    from app.services.byok.provider_clients import SOURCE_PLATFORM, CredentialUse, ProviderClientFactory

    built: list[str] = []
    org = uuid.uuid4()

    def build(db, *, organization_id, provider, prefer_tenant_key=True):
        built.append(provider)
        return object(), CredentialUse(source=SOURCE_PLATFORM, provider=provider.upper(), organization_id=org)

    seen: list[str] = []

    def adapter(client, *, prompt, temperature, ai_settings):
        seen.append(ai_settings.model)
        yield llm_stream.StreamChunk(text="ok")

    monkeypatch.setattr(ProviderClientFactory, "build", staticmethod(build))
    from app.core.byok_providers import PROVIDER_GEMINI, PROVIDER_GROQ

    monkeypatch.setitem(llm_stream._STREAM_ADAPTERS, PROVIDER_GROQ, adapter)
    monkeypatch.setitem(llm_stream._STREAM_ADAPTERS, PROVIDER_GEMINI, adapter)

    session = llm_stream.open_stream(
        None, organization_id=org, prompt="Hello", temperature=0.2,
        ai_settings=_settings(AIProvider.GEMINI, "gemini-2.5-pro"),
    )
    assert [chunk.text for chunk in session.chunks] == ["ok"]
    assert seen == [PLATFORM_DEFAULT_MODEL]
    assert built[-1] == PROVIDER_GROQ
