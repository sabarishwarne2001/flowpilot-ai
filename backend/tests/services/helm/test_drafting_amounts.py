"""An amount the model writes that no one could hold is unknown, not a server error.

A sentence like "hold any invoice above 100000000000000000000000 rupees" made the drafting model
write that number; turning it into millionths needed more digits than the decimal context holds
and raised, so POST …/mandates/draft answered 500 instead of the validator asking for an amount.
"Infinity" and "NaN" did the same.
"""

from __future__ import annotations

import pytest

from app.services.helm import drafting, money

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("written", ["100000000000000000000000", "Infinity", "-Infinity", "NaN", "1e400"])
def test_an_amount_no_one_could_hold_is_unknown(written: str) -> None:
    assert money.to_micros(written) is None
    assert drafting._written_money({"amount": written, "currency": "inr"}) == {"amount_micros": None,
                                                                              "currency": "INR"}


def test_an_ordinary_amount_still_converts() -> None:
    assert drafting._written_money({"amount": "5,00,000", "currency": "inr"}) == {"amount_micros": 500_000_000_000,
                                                                                 "currency": "INR"}
