"""Approval chains, the pure part (`helm/approvals.py`): reading a chain a person typed, in words when
it cannot be kept, and the one requirement every approving path reads."""

from __future__ import annotations

import pytest

from app.services.helm import approvals as chains
from app.services.helm import ledger, plan_store
from app.services.helm import vocabulary as v

pytestmark = pytest.mark.no_db

M = 1_000_000
LAKH = 100_000 * M


def tier(above=LAKH, currency="INR", approvals=2, roles=None):
    return {"above_micros": above, "currency": currency, "approvals": approvals, "roles": roles}


def problems_of(raw, **kwargs) -> list[str]:
    with pytest.raises(chains.ChainInvalid) as caught:
        chains.validate(raw, **kwargs)
    return caught.value.problems


def test_a_typed_chain_is_cleaned_and_ordered():
    tiers = chains.validate([tier(above=10 * LAKH, approvals=3), tier(currency="inr", roles=["admin", "OWNER"]),
                             tier(above=0, currency="usd")])
    assert tiers == (
        chains.Tier(above_micros=LAKH, currency="INR", approvals=2, roles=("OWNER", "ADMIN")),
        chains.Tier(above_micros=10 * LAKH, currency="INR", approvals=3),
        chains.Tier(above_micros=0, currency="USD", approvals=2),
    )
    assert tiers[0].as_json() == {"above_micros": LAKH, "currency": "INR", "approvals": 2,
                                  "roles": ["OWNER", "ADMIN"]}
    assert tiers[1].as_json()["roles"] is None
    assert chains.validate([]) == ()


@pytest.mark.parametrize("raw, problem", [
    (tier(approvals=1), "Tier 1: a chain asks for two or three approvals."),
    (tier(approvals=4), "Tier 1: a chain asks for two or three approvals."),
    (tier(approvals=True), "Tier 1: a chain asks for two or three approvals."),
    (tier(currency="RS"), "Tier 1: a currency is three letters, such as INR."),
    (tier(above=-1), "Tier 1: the amount is zero or more, written as a number."),
    (tier(above="100000"), "Tier 1: the amount is zero or more, written as a number."),
    (tier(roles=["MEMBER"]), "Tier 1: approvals come from owners, admins or the billing role."),
    (tier(roles="OWNER"), "Tier 1: the roles are a list, such as owners and admins."),
])
def test_a_tier_that_cannot_be_kept_says_why(raw, problem):
    assert problems_of([raw]) == [problem]


def test_a_contradictory_chain_says_why_in_words():
    assert problems_of([tier(), tier(approvals=3)]) == ["Two tiers start above ₹1 lakh; keep one."]
    assert problems_of([tier(above=LAKH, approvals=3), tier(above=10 * LAKH, approvals=2)]) == [
        "Above ₹10 lakh needs at least as many approvals as above ₹1 lakh."]
    # Different currencies never compare.
    assert len(chains.validate([tier(above=LAKH, approvals=3), tier(above=10 * LAKH, approvals=2, currency="USD")]))
    assert problems_of([tier(above=n * LAKH) for n in range(1, 8)]) == ["An approval chain has at most six tiers."]
    assert problems_of("not a list") == ["An approval chain is a list of tiers."]


def test_a_chain_nobody_could_meet_is_refused():
    people = {"OWNER": 1, "ADMIN": 1, "MEMBER": 5}
    assert chains.validate([tier(approvals=2)], people=people)
    assert problems_of([tier(approvals=3)], people=people) == [
        "Above ₹1 lakh the chain needs three different people from owners, admins or the billing role, and your "
        "organization has two: those payments could never be approved."]
    assert problems_of([tier(approvals=2, roles=["OWNER"])], people=people) == [
        "Above ₹1 lakh the chain needs two different people from owners, and your organization has one: those "
        "payments could never be approved."]


def test_a_kept_chain_nobody_can_meet_any_more_is_said_not_refused():
    tiers = chains.validate([tier(approvals=2, roles=["OWNER", "ADMIN"]), tier(above=10 * LAKH, approvals=3)])
    assert chains.warnings(tiers, {"OWNER": 1, "ADMIN": 1, "BILLING": 1}) == []
    assert chains.warnings(tiers, {"OWNER": 1, "BILLING": 1}) == [
        "Above ₹1 lakh the chain needs two different people from owners or admins, and your organization has one: "
        "those payments wait until someone is added or the chain is changed.",
        "Above ₹10 lakh the chain needs three different people from owners, admins or the billing role, and your "
        "organization has two: those payments wait until someone is added or the chain is changed."]
    # Shown only where the chain applies, and only when the people are known.
    assert chains.chain_json(tiers, available=True, people={"OWNER": 1})["warnings"] != []
    assert chains.chain_json(tiers, available=False, people={"OWNER": 1})["warnings"] == []
    assert chains.chain_json(tiers, available=True)["warnings"] == []


def test_the_requirement_is_the_highest_tier_below_the_amount():
    tiers = chains.validate([tier(above=LAKH), tier(above=10 * LAKH, approvals=3, roles=["OWNER", "ADMIN"]),
                             tier(above=0, currency="USD")])
    pay = v.ACTION_SCHEDULE_PAYMENT
    assert chains.requirement(tiers, kind=pay, amount_micros=LAKH, currency="INR") == chains.Requirement()
    two = chains.requirement(tiers, kind=pay, amount_micros=LAKH + 1, currency="INR")
    assert (two.approvals, two.roles) == (2, ())
    three = chains.requirement(tiers, kind=v.ACTION_APPROVE_PAYMENT, amount_micros=50 * LAKH, currency="INR")
    assert (three.approvals, three.roles) == (3, ("OWNER", "ADMIN"))
    assert chains.requirement(tiers, kind=pay, amount_micros=1, currency="USD").approvals == 2
    assert chains.requirement(tiers, kind=pay, amount_micros=50 * LAKH, currency="EUR").approvals == 1
    assert chains.requirement(tiers, kind=pay, amount_micros=None, currency="INR").approvals == 1
    # A hold, a release and a recorded payment move no money to anyone: one approval, as always.
    for kind in (v.ACTION_HOLD_PAYMENT, v.ACTION_RELEASE_HOLD, v.ACTION_MARK_PAID, v.ACTION_SEND_REMINDER):
        assert chains.requirement(tiers, kind=kind, amount_micros=50 * LAKH, currency="INR") == chains.Requirement()
    assert chains.requirement((), kind=pay, amount_micros=50 * LAKH, currency="INR") == chains.Requirement()


def test_only_people_from_the_chains_roles_count():
    need = chains.Requirement(approvals=2, roles=("OWNER", "ADMIN"))
    given = [{"by": "helm", "at": "t"}, {"user_id": "a", "role": "OWNER"}, {"user_id": "b", "role": "BILLING"}]
    assert need.counted(given) == [{"user_id": "a", "role": "OWNER"}]
    assert need.allows("ADMIN") and not need.allows("BILLING")
    anyone = chains.Requirement()
    assert anyone.allows("BILLING") and len(anyone.counted(given)) == 2


def test_the_stored_chain_is_read_tolerantly():
    assert chains.parse(None) == () and chains.parse({"tiers": "x"}) == ()
    good = tier()
    assert chains.parse({"tiers": [good, {"currency": "INR"}, "junk"]}) == (chains.Tier(LAKH, "INR", 2),)


def test_each_tier_reads_as_a_sentence():
    one, two = chains.validate([tier(roles=["OWNER", "ADMIN"]), tier(above=10 * LAKH, approvals=3)])
    assert chains.describe(one) == "Above ₹1 lakh: two approvals from different people (owners or admins)."
    assert chains.describe(two) == ("Above ₹10 lakh: three approvals from different people (owners, admins or "
                                    "the billing role).")
    assert chains.chain_json([one], available=True)["sentences"] == [chains.describe(one)]


def test_a_chain_is_a_plan_input_and_no_chain_keeps_the_old_digest():
    old = ledger.sha256({"snapshot": "s", "engine": v.ENGINE_VERSION, "mandates": []})
    assert plan_store.plan_digest("s", []) == old
    chained = plan_store.plan_digest("s", [], chain=chains.validate([tier()]))
    assert chained != old
    assert chained != plan_store.plan_digest("s", [], chain=chains.validate([tier(approvals=3)]))
