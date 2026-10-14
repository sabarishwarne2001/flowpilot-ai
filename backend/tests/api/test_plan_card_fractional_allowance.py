"""The plan cards carry a fractional allowance as it is published (campaign session 4).

    pytest tests/api/test_plan_card_fractional_allowance.py -q

Free stores 0.25 GB (`storage.gb_month`, TENANCY-AND-PLANS.md §4.2). `GET .../billing/plans`
cast every allowance with `int()`, so the plan card received 0 and showed "0 MB document
storage" for Free, where the card's own formatter turns 0.25 into "256 MB". Whole numbers stay
whole numbers.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.security.plans import put_on_plan


def _entitlements(client, tenant, plan_key: str) -> dict[str, object]:
    response = client.get(
        f"/api/v1/organizations/{tenant.organization.id}/billing/plans", headers=tenant.owner.headers
    )
    assert response.status_code == 200, response.text
    plan = next(p for p in response.json()["plans"] if p["key"] == plan_key)
    return {entry["event_type"]: entry["limit_quantity"] for entry in plan["entitlements"]}


def test_free_storage_reaches_the_plan_card_as_a_quarter_of_a_gigabyte(client, db_session: Session, tenant) -> None:
    put_on_plan(db_session, tenant.organization, "free")
    quantities = _entitlements(client, tenant, "free")
    assert quantities["storage.gb_month"] == 0.25


def test_whole_allowances_stay_whole_numbers(client, db_session: Session, tenant) -> None:
    put_on_plan(db_session, tenant.organization, "free")
    quantities = _entitlements(client, tenant, "free")
    assert quantities["document.upload"] == 25
    assert isinstance(quantities["document.upload"], int)
    assert quantities["limit.seats"] == 2
    assert isinstance(quantities["limit.seats"], int)
