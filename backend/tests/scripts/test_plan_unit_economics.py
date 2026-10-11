"""Every plan keeps a healthy gross margin even when used to its limits (campaign session 1).

    pytest tests/scripts/test_plan_unit_economics.py -q

The cost of one seat (one organization on Free) using every allowance to the full,
priced at the price book's own cost bases (`scripts/seed_price_book.py`, the figures
the margin service reports COGS from), against the plan's price per seat:

  * OCR pages at the self-hosted OCR cost basis;
  * input and output tokens at the platform default model's cost basis
    (`openai/gpt-oss-20b` on Groq);
  * storage at the object storage cost basis per GB-month;
  * embedding: every OCR page indexed at ~500 tokens.

A paid plan must keep at least 60% gross margin at its ceilings (typical use is far
below them), and Free must cost less than $0.25 a month per account: it is an
acquisition cost, not a product. Changing a limit or a cost basis so that this no
longer holds needs a decision, not a quiet edit.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.security.plans import _price_book_seed, _seed

#: Tokens written to the embedding index per OCR page (a page of text).
EMBEDDING_TOKENS_PER_PAGE = 500
MIN_PAID_GROSS_MARGIN = Decimal("0.60")
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


def cost_at_ceilings_usd(plan: str) -> Decimal:
    entries = {row["limit_key"]: row for row in _seed().PLACEHOLDER_TIERS[plan]["entries"]}
    pages = _quantity(entries, "ocr.page")
    micros = (
        pages * _cost_basis("ocr.page", "paddleocr")
        + _quantity(entries, "llm.input_token") * _cost_basis("llm.input_token", "groq", "openai/gpt-oss-20b")
        + _quantity(entries, "llm.output_token") * _cost_basis("llm.output_token", "groq", "openai/gpt-oss-20b")
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


def test_free_costs_little_even_used_to_its_limits() -> None:
    cost = cost_at_ceilings_usd("free")
    assert cost <= MAX_FREE_MONTHLY_COST_USD, f"Free costs ${cost:.2f} a month at its limits"
