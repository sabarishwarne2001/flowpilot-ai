"""Every unit a paid plan bills past its allowance has a price, and the price covers its cost (campaign session 1, D.3).

    pytest tests/scripts/test_every_overage_is_priced.py -q

A plan row with ALLOW_AND_BILL lets usage continue past the allowance and bills each unit
above it at the price book's `<event>.overage` price for the provider that served it
(`quota_service.bill_overage_if_any`). Where no such price exists the platform logs
`quota.overage_unpriced` and bills nothing: the customer keeps going and the platform
pays the supplier. So, for every ALLOW_AND_BILL row on every plan and every provider the
PLATFORM pays for that event (it holds the provider's key, `byok_providers.platform_key_for`,
or the provider is internal), there must be an overage price, and it must be at least
twice the highest cost basis that provider has for the event (a 50% gross margin on the
overage itself). Providers served only on the customer's own key bill no overage (N-048).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core import byok_providers
from tests.security.plans import _price_book_seed, _seed

ZERO_COST_SOURCES = {"ZERO_BYOK"}
MIN_OVERAGE_MARKUP = Decimal(2)


def _billed_rows() -> list[tuple[str, str, str]]:
    rows = []
    for plan, definition in _seed().PLACEHOLDER_TIERS.items():
        for entry in definition["entries"]:
            if entry.get("overage_policy") == "ALLOW_AND_BILL":
                rows.append((plan, entry["limit_key"], entry.get("overage_price_tier_key")))
    return rows


def _platform_pays(provider: str) -> bool:
    try:
        return byok_providers.platform_key_for(provider) is not None
    except (KeyError, ValueError):
        return True  # not a model provider (OCR, storage): the platform's own cost


def _paid_costs(event_type: str) -> dict[str, Decimal]:
    """The highest cost basis per provider the platform actually pays for this event."""
    costs: dict[str, Decimal] = {}
    for row in _price_book_seed().PLACEHOLDER_ENTRIES:
        if row["event_type"] != event_type or row.get("tier_key") is not None:
            continue
        if not _platform_pays(row["provider"]):
            continue
        if row.get("cost_basis_source") in ZERO_COST_SOURCES or row.get("cost_basis_micros") is None:
            continue
        provider = row["provider"]
        costs[provider] = max(costs.get(provider, Decimal(0)), Decimal(str(row["cost_basis_micros"])))
    return costs


def _overage_price(event_type: str, provider: str, tier_key: str) -> Decimal | None:
    for row in _price_book_seed().PLACEHOLDER_ENTRIES:
        if (
            row["event_type"] == f"{event_type}.overage"
            and row["provider"] == provider
            and row.get("model") is None
            and row.get("tier_key") == tier_key
        ):
            return Decimal(str(row["unit_price_micros"]))
    return None


def test_the_plans_do_bill_overage_somewhere() -> None:
    assert len(_billed_rows()) >= 6


@pytest.mark.parametrize(("plan", "limit_key", "tier_key"), _billed_rows())
def test_every_billed_overage_has_a_price_that_covers_its_cost(plan: str, limit_key: str, tier_key: str) -> None:
    costs = _paid_costs(limit_key)
    assert costs, f"{plan}: {limit_key} bills overage but nothing meters it at a cost"
    problems = []
    for provider, cost in sorted(costs.items()):
        price = _overage_price(limit_key, provider, tier_key)
        if price is None:
            problems.append(f"{provider}: no {limit_key}.overage price (usage past the allowance is never billed)")
        elif price < cost * MIN_OVERAGE_MARKUP:
            problems.append(f"{provider}: overage {price} micros against a cost of {cost} (under 2x)")
    assert not problems, f"{plan} / {limit_key}:\n  " + "\n  ".join(problems)
