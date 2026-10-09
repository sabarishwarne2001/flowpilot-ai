"""AI Settings showed a price-book refusal on every model, and offered two Groq
models (`groq/compound`, `groq/compound-mini`) that the Groq API answers with
404 model_not_found.

The refusal: `_pricing` asked the price book for `llm.tokens_in`, an event type
the book never publishes (it prices `llm.input_token` / `llm.output_token`), so
every lookup missed both the model row and the provider-wide default.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.ai_models import AI_MODELS
from app.core.config import Settings
from app.schemas.ai_settings import AIProvider
from app.services import ai_settings_resolution, pricing_service

PHANTOM = {"groq/compound", "groq/compound-mini"}


def test_registry_lists_no_phantom_groq_models():
    assert not PHANTOM & set(AI_MODELS[AIProvider.GROQ])


def test_primary_groq_default_is_gpt_oss_120b():
    assert AI_MODELS[AIProvider.GROQ][0] == "openai/gpt-oss-120b"
    assert Settings.model_fields["GROQ_MODEL_NAME"].default == "openai/gpt-oss-120b"


def test_seed_publishes_no_phantom_groq_rows():
    from scripts.seed_price_book import PLACEHOLDER_ENTRIES

    assert not {e["model"] for e in PLACEHOLDER_ENTRIES} & PHANTOM


def test_seed_prices_every_registered_groq_model_and_a_provider_default():
    from scripts.seed_price_book import PLACEHOLDER_ENTRIES

    groq = {(e["event_type"], e["model"]) for e in PLACEHOLDER_ENTRIES
            if e["provider"] == "groq" and "tier_key" not in e}
    for event_type in ("llm.input_token", "llm.output_token"):
        assert (event_type, None) in groq
        for model in AI_MODELS[AIProvider.GROQ]:
            assert (event_type, model) in groq, (event_type, model)


def test_settings_price_check_asks_for_the_event_type_the_book_publishes(monkeypatch):
    seen = []

    def fake_resolve(db, *, event_type, provider, model, at):
        seen.append(event_type)
        if event_type != "llm.input_token":
            raise RuntimeError(f"no entry for {event_type}")
        return SimpleNamespace(unit_price_micros="0.6", currency="USD",
                               price_book_version=2, fallback=False)

    monkeypatch.setattr(pricing_service, "resolve", fake_resolve)
    pricing, warning = ai_settings_resolution._pricing(
        None, provider="GROQ", model_name="openai/gpt-oss-120b"
    )
    assert warning is None, warning
    assert seen == ["llm.input_token"]
    assert pricing.price_per_1m_input_micros == 600_000
