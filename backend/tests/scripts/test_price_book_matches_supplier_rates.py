"""The price book's cost of every model is at least what the supplier charges (campaign session 2, F-261).

    pytest tests/scripts/test_price_book_matches_supplier_rates.py -q

The margin checks (test_plan_unit_economics.py) and the margin service read COGS from the
price book's `cost_basis_micros`. They were set from old rate cards: GPT-OSS 20B output at
$0.10 per million tokens (Groq charges $0.30), Qwen 3.8 27B at $0.08 / $0.16 (Groq: $0.80 /
$4.00), and every Gemini model at the provider default of $0.075 / $0.30 (Gemini 2.5 Pro is
$1.25 / $10.00). So the margins the plans were sized on were overstated, and the platform sold
some models below their cost.

SUPPLIER_RATES is the suppliers' published pay-as-you-go list price per million tokens
(input, output), as checked on 2026-10-11 (Groq and Google's pricing pages, as reported by
eesel.ai/blog/groq-pricing and morphllm.com/gemini-api-pricing). When a supplier changes a
price, change it here first: this test then says which price-book rows to move.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.ai_models import AI_MODELS
from tests.security.plans import _price_book_seed

#: (provider, model) -> (input, output) in US dollars per million tokens.
SUPPLIER_RATES: dict[tuple[str, str], tuple[Decimal, Decimal]] = {
    ("groq", "openai/gpt-oss-20b"): (Decimal("0.075"), Decimal("0.30")),
    ("groq", "openai/gpt-oss-120b"): (Decimal("0.15"), Decimal("0.60")),
    ("groq", "qwen/qwen3.8-27b"): (Decimal("0.80"), Decimal("4.00")),
    ("gemini", "gemini-2.5-flash"): (Decimal("0.30"), Decimal("2.50")),
    ("gemini", "gemini-2.5-pro"): (Decimal("1.25"), Decimal("10.00")),
    ("gemini", "gemini-3.5-flash"): (Decimal("1.50"), Decimal("9.00")),
}

EVENTS = ("llm.input_token", "llm.output_token")


def _row(event_type: str, provider: str, model: str | None) -> dict | None:
    for row in _price_book_seed().PLACEHOLDER_ENTRIES:
        if (
            row["event_type"] == event_type
            and row.get("provider") == provider
            and row.get("model") == model
            and row.get("tier_key") is None
        ):
            return row
    return None


def _cost(event_type: str, provider: str, model: str) -> Decimal:
    """The cost basis the platform's pricing resolves for this model (its own row, else the provider's)."""
    row = _row(event_type, provider, model) or _row(event_type, provider, None)
    assert row is not None and row.get("cost_basis_micros") is not None, f"no cost for {provider} {model}"
    return Decimal(str(row["cost_basis_micros"]))  # micro-dollars per token = dollars per million tokens


def test_every_listed_model_has_a_supplier_rate() -> None:
    listed = {(provider.value.lower(), model) for provider, models in AI_MODELS.items() for model in models}
    missing = listed - set(SUPPLIER_RATES)
    assert not missing, f"listed models with no supplier rate recorded here: {sorted(missing)}"


@pytest.mark.parametrize(("provider", "model"), sorted(SUPPLIER_RATES))
@pytest.mark.parametrize("event_type", EVENTS)
def test_the_cost_basis_is_at_least_the_suppliers_price(provider: str, model: str, event_type: str) -> None:
    supplier = SUPPLIER_RATES[(provider, model)][EVENTS.index(event_type)]
    cost = _cost(event_type, provider, model)
    assert cost >= supplier, (
        f"{provider} {model} {event_type}: the price book costs it at ${cost} per million tokens; "
        f"the supplier charges ${supplier}"
    )


@pytest.mark.parametrize(("provider", "model"), sorted(SUPPLIER_RATES))
@pytest.mark.parametrize("event_type", EVENTS)
def test_the_price_is_at_least_twice_the_cost(provider: str, model: str, event_type: str) -> None:
    row = _row(event_type, provider, model) or _row(event_type, provider, None)
    price = Decimal(str(row["unit_price_micros"]))
    assert price >= 2 * _cost(event_type, provider, model), f"{provider} {model} {event_type} sells at ${price}"


@pytest.mark.parametrize("provider", ["groq", "gemini"])
@pytest.mark.parametrize("event_type", EVENTS)
def test_a_providers_default_row_covers_its_dearest_listed_model(provider: str, event_type: str) -> None:
    """An unlisted model of a provider is priced by the provider's default row: never below the dearest."""
    dearest = max(rates[EVENTS.index(event_type)] for (p, _), rates in SUPPLIER_RATES.items() if p == provider)
    row = _row(event_type, provider, None)
    assert row is not None
    assert Decimal(str(row["cost_basis_micros"])) >= dearest
