"""The cash floor the simulation measures: with two "keep cash above" mandates in one currency, the
probability on the flight path is the chance of staying above the HIGHER floor (the line the cockpit
draws, and the floor the optimiser plans against), whichever mandate was created first."""

from __future__ import annotations

import pytest

from app.services.helm import planner
from app.services.helm import snapshot as snap
from app.services.helm import vocabulary as v
from tests.services.helm.test_guard import M, TODAY, mandate
from tests.services.helm.test_library import full_book

pytestmark = pytest.mark.no_db


def _cash(amount: int) -> dict[str, snap.Cash]:
    return {"INR": snap.Cash(currency="INR", amount_micros=amount, as_of=TODAY, source=v.CASH_SOURCE_MANUAL,
                             age_days=0)}


@pytest.mark.parametrize("order", ["high_first", "low_first"])
def test_the_flight_paths_probability_is_measured_against_the_highest_floor(order):
    high = {"floor": {"amount_micros": 450_000 * M, "currency": "INR"}}
    low = {"floor": {"amount_micros": 100_000 * M, "currency": "INR"}}
    first, second = (high, low) if order == "high_first" else (low, high)
    book = full_book(cash=_cash(400_000 * M))  # below the higher floor today, far above the lower one
    composition = planner.compose(book, [mandate("keep_cash_above", first, n=1),
                                         mandate("keep_cash_above", second, n=2)])
    assert composition.forecast_sim.floor_probability == {"INR": 0.0}
    assert composition.sim.floor_probability == {"INR": 0.0}
    assert planner.cash_floors(composition.mandates) == {"INR": 450_000 * M}
