"""Every plan keeps a healthy gross margin even when used to its limits (campaign session 1).

    pytest tests/scripts/test_plan_unit_economics.py -q

The cost of one seat (one organization on Free) using every allowance to the full,
priced at the price book's own cost bases (`scripts/seed_price_book.py`, the figures
the margin service reports COGS from), against the plan's price per seat:

  * OCR pages at the self-hosted OCR cost basis;
  * input and output tokens at the cost basis of the model a new workspace runs
    (`AI_MODELS[GROQ][0]`, `openai/gpt-oss-120b`; campaign session 2);
  * storage at the object storage cost basis per GB-month;
  * embedding: every OCR page indexed at ~500 tokens.

A paid plan must keep at least 60% gross margin at its ceilings (typical use is far
below them), and Free must cost less than $0.25 a month per account: it is an
acquisition cost, not a product. Changing a limit or a cost basis so that this no
longer holds needs a decision, not a quiet edit.

The platform also serves a dearer model on its own keys (GPT-OSS 120B), so a seat whose
tokens all go to the dearest model it serves must still keep at least 50% (N-046). Premium
models (Gemini, Groq's Preview models) run on the organization's own key (N-054).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core import byok_providers
from app.core.ai_models import AI_MODELS, PLATFORM_SERVED_MODELS
from app.schemas.ai_settings import AIProvider
from tests.security.plans import _price_book_seed, _seed

#: Tokens written to the embedding index per OCR page (a page of text).
EMBEDDING_TOKENS_PER_PAGE = 500
MIN_PAID_GROSS_MARGIN = Decimal("0.60")
MIN_PAID_GROSS_MARGIN_DEAREST_MODEL = Decimal("0.50")
MAX_FREE_MONTHLY_COST_USD = Decimal("0.25")


def _cost_basis(event_type: str, provider: str, model: str | None = None) -> Decimal:
    for row in _price_book_seed().PLACEHOLDER_ENTRIES:
        if (
            row["event_type"] == event_type
            and row.get("provider") == provider
            and row.get("model") == model
            and row.get("tier_key") is None
            and row.get("cost_basis_micros") is not None
        ):
            return Decimal(str(row["cost_basis_micros"]))
    raise AssertionError(f"no cost basis for {event_type} / {provider} / {model}")


def _quantity(entries: dict, key: str) -> Decimal:
    row = entries.get(key)
    return Decimal(str(row["max_quantity"])) if row and row.get("max_quantity") is not None else Decimal(0)


def _dearest_platform_cost(event_type: str) -> Decimal:
    """The highest cost basis among the models the platform pays for.

    Campaign session 2 (N-054): the platform's own keys serve only PLATFORM_SERVED_MODELS (a
    call with any other model runs on the organization's key or is answered by the platform
    default, `tests/services/test_platform_served_models.py`). Before, every model of a
    provider whose key the platform holds counted, and at the suppliers' real prices
    (`test_price_book_matches_supplier_rates.py`) Gemini 2.5 Pro made an Enterprise seat cost
    $2,223 a month: exactly why those models moved to the customer's key.
    """
    served = [
        (provider, model)
        for provider, models in PLATFORM_SERVED_MODELS.items()
        for model in models
        if byok_providers.platform_key_for(provider) is not None
    ]
    assert served, "the platform serves no model on its own keys"
    return max(_cost_basis(event_type, provider, model) for provider, model in served)


def cost_at_ceilings_usd(plan: str, *, dearest_model: bool = False) -> Decimal:
    entries = {row["limit_key"]: row for row in _seed().PLACEHOLDER_TIERS[plan]["entries"]}
    pages = _quantity(entries, "ocr.page")
    if dearest_model:
        input_cost = _dearest_platform_cost("llm.input_token")
        output_cost = _dearest_platform_cost("llm.output_token")
    else:
        # Campaign session 2: the model a new workspace actually runs (the primary default,
        # `AI_MODELS[GROQ][0]`, GPT-OSS 120B since commit 233a3c0), not the cheaper 20B
        # this test assumed.
        default_model = AI_MODELS[AIProvider.GROQ][0]
        input_cost = _cost_basis("llm.input_token", "groq", default_model)
        output_cost = _cost_basis("llm.output_token", "groq", default_model)
    micros = (
        pages * _cost_basis("ocr.page", "paddleocr")
        + _quantity(entries, "llm.input_token") * input_cost
        + _quantity(entries, "llm.output_token") * output_cost
        + _quantity(entries, "storage.gb_month") * _cost_basis("storage.gb_month", "internal")
        + pages * EMBEDDING_TOKENS_PER_PAGE * _cost_basis("embedding.token", "sentence_transformers")
    )
    return micros / Decimal(1_000_000)


@pytest.mark.parametrize("plan", ["developer", "business", "enterprise"])
def test_a_paid_seat_used_to_its_limits_keeps_a_healthy_margin(plan: str) -> None:
    price = Decimal(_seed().COMMERCIALS[plan]["unit_amount_micros"]) / Decimal(1_000_000)
    cost = cost_at_ceilings_usd(plan)
    margin = (price - cost) / price
    assert margin >= MIN_PAID_GROSS_MARGIN, (
        f"{plan}: ${cost:.2f} of cost at the ceilings against ${price:.2f} per seat is a "
        f"{margin:.0%} gross margin (under {MIN_PAID_GROSS_MARGIN:.0%})"
    )


@pytest.mark.parametrize("plan", ["developer", "business", "enterprise"])
def test_a_paid_seat_on_the_dearest_platform_model_still_keeps_half(plan: str) -> None:
    price = Decimal(_seed().COMMERCIALS[plan]["unit_amount_micros"]) / Decimal(1_000_000)
    cost = cost_at_ceilings_usd(plan, dearest_model=True)
    margin = (price - cost) / price
    assert margin >= MIN_PAID_GROSS_MARGIN_DEAREST_MODEL, (
        f"{plan}: ${cost:.2f} of cost at the ceilings on the dearest platform model against "
        f"${price:.2f} per seat is a {margin:.0%} gross margin (under {MIN_PAID_GROSS_MARGIN_DEAREST_MODEL:.0%})"
    )


def test_free_costs_little_even_used_to_its_limits() -> None:
    cost = cost_at_ceilings_usd("free")
    assert cost <= MAX_FREE_MONTHLY_COST_USD, f"Free costs ${cost:.2f} a month at its limits"
