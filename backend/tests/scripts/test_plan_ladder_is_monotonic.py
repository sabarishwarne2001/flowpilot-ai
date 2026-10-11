"""A higher plan is never worse than a lower one, in any dimension (campaign session 1).

    pytest tests/scripts/test_plan_ladder_is_monotonic.py -q

Read over the seed the deploy publishes (`scripts/seed_quota_tiers.py`), Free ->
Developer -> Business -> Enterprise, for every row the lower plan carries:

  * a capability or add-on the lower plan has, the higher plan has;
  * a monthly allowance is at least as large (an absent row on the higher plan means
    no ceiling, which is larger), and its overage policy is at least as permissive
    (refuse < bill overage < warn);
  * a static limit (`limit.*`) is at least as large, absent meaning no limit of the
    plan's own (the platform maximum, or purchased seats);
  * the price per seat is at least as high (so nobody pays more for less).

Paid allowances are per seat and Free's are per organization, so one seat of a paid
plan is compared with Free: the smallest paid organization must beat Free.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.security.plans import _seed

LADDER = ("free", "developer", "business", "enterprise")
POLICY_RANK = {"REFUSE": 0, "ALLOW_AND_BILL": 1, "ALLOW_AND_WARN": 2}


def _rows(key: str) -> dict[str, dict]:
    return {row["limit_key"]: row for row in _seed().PLACEHOLDER_TIERS[key]["entries"]}


def _ceiling(row: dict) -> Decimal | None:
    if row.get("max_quantity") is not None:
        return Decimal(str(row["max_quantity"]))
    if row.get("max_cost_micros") is not None and not str(row["limit_key"]).startswith(("capability.", "addon.", "llm.platform_key")):
        return Decimal(int(row["max_cost_micros"]))
    return None


@pytest.mark.parametrize("lower,higher", list(zip(LADDER, LADDER[1:])))
def test_each_plan_is_at_least_as_generous_as_the_one_below(lower: str, higher: str) -> None:
    below, above = _rows(lower), _rows(higher)
    problems: list[str] = []
    for key, row in below.items():
        is_grant = key.startswith(("capability.", "addon.")) or key == "llm.platform_key"
        if is_grant:
            if key not in above:
                problems.append(f"{higher} drops the {key} that {lower} has")
            continue
        if key not in above:
            continue  # no ceiling of its own on the higher plan: more generous
        lower_ceiling, higher_ceiling = _ceiling(row), _ceiling(above[key])
        if lower_ceiling is not None and higher_ceiling is not None and higher_ceiling < lower_ceiling:
            problems.append(f"{higher} {key} {higher_ceiling} is below {lower}'s {lower_ceiling}")
        if key.startswith("limit."):
            continue
        rank_low = POLICY_RANK[row.get("overage_policy", "REFUSE")]
        rank_high = POLICY_RANK[above[key].get("overage_policy", "REFUSE")]
        if rank_high < rank_low:
            problems.append(
                f"{higher} {key} is {above[key].get('overage_policy')} where {lower} is {row.get('overage_policy')}"
            )
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("lower,higher", list(zip(LADDER, LADDER[1:])))
def test_a_higher_plan_never_costs_less_per_seat(lower: str, higher: str) -> None:
    commercials = _seed().COMMERCIALS
    assert commercials[higher]["unit_amount_micros"] >= commercials[lower]["unit_amount_micros"]


def test_the_free_plan_is_the_small_one_the_owner_asked_for() -> None:
    """Campaign session 1: the target is a taste, not a free product."""
    free = _rows("free")
    assert free["document.upload"]["max_quantity"] == "25"
    assert free["ocr.page"]["max_quantity"] == "50"
    assert free["assistant.message"]["max_quantity"] == "30"
    assert free["limit.seats"]["max_quantity"] == "2"
    assert free["limit.workspaces"]["max_quantity"] == "1"
    assert free["limit.file_size_mb"]["max_quantity"] == "10"
    assert free["limit.pages_per_document"]["max_quantity"] == "10"
    assert Decimal(free["storage.gb_month"]["max_quantity"]) == Decimal("0.25")
    for key, row in free.items():
        if not key.startswith(("capability.", "addon.", "limit.")) and key != "llm.platform_key":
            assert row.get("overage_policy", "REFUSE") == "REFUSE", f"Free {key} must stop, not bill or warn"
    assert not [key for key in free if key.startswith(("capability.", "addon."))], "Free has no premium capability"
