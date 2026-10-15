"""Custom condition mandates (Enterprise, brief §5.2 "custom_condition"): the owner's own rule over an
invoice's facts. Its validator and readback in words, the evaluator over every fact it may read, its
proposals and Guard checks for each outcome, its holds and releases in the planner, its backtest, and
that a plan without `capability.helm_custom` never gets it. Pure: snapshots, no database."""

from __future__ import annotations

import random
import uuid
from datetime import timedelta

import pytest

from app.services.helm import drafting
from app.services.helm import msme as msme_rules
from app.services.helm import planner
from app.services.helm import snapshot as snap
from app.services.helm import vocabulary as v
from app.services.helm.guard import Actor, GuardContext, SYSTEM, guard
from app.services.helm.history import History
from app.services.helm.mandates import base, registry
from app.services.helm.mandates.library import custom_condition as cc
from app.services.helm.mandates.spec import MandateSpec
from tests.services.helm.test_guard import M, TODAY, action, mandate, payable
from tests.services.helm.test_library import BANK, full_book

pytestmark = pytest.mark.no_db

KEY = "custom_condition"
LAKH = 100_000 * M


def money(amount: int, currency: str = "INR") -> dict:
    return {"amount_micros": amount, "currency": currency}


def rule(field: str, op: str, value) -> dict:
    return {"field": field, "op": op, "value": value}


def params(*conditions, name="Large invoices from new suppliers", match="all", outcome="hold") -> dict:
    return {"name": name, "match": match, "outcome": outcome, "conditions": list(conditions)}


BIG_AND_NEW = params(rule("amount", ">", money(5 * LAKH)), rule("new_supplier", "=", True))


def custom(p: dict, *, n: int = 1, mode: str = "propose"):
    return mandate(KEY, p, mode=mode, n=n)


def problems(p: dict, *, enterprise: bool = True) -> list[str]:
    try:
        spec = registry.normalized(MandateSpec(template=KEY, kind="limit", params=p))
    except registry.MandateError as exc:
        return exc.problems
    facts = registry.BookFacts(workspace_ids=frozenset(), counterparty_keys=frozenset(), currencies=frozenset(),
                               enterprise=enterprise)
    return registry.validate(spec, facts)


def sentence(p: dict) -> str:
    m = custom(p)
    return registry.template(KEY).readback(m.spec, m.params).sentence


# ---------------------------------------------------------------------------
# Readback and validation, in words
# ---------------------------------------------------------------------------


def test_it_reads_back_in_plain_words_for_each_outcome():
    assert sentence(BIG_AND_NEW) == ("Hold any invoice where the amount is above ₹5 lakh and the supplier is new; "
                                     "release it once that is no longer true.")
    assert sentence(params(rule("days_until_due", "<", 5), rule("confidence", "=", "EXTRACTED"),
                           rule("counterparty", "contains", "steel"), match="any", outcome="approval")) == (
        "Ask a person before paying any invoice where it is due in fewer than 5 days, its facts are read by the "
        "model and not checked or the supplier's name contains \"steel\".")
    assert sentence(params(rule("has_matched_po", "!=", True), rule("currency", "in", ["usd", "EUR"]),
                           rule("days_since_invoice", ">=", 1), outcome="notify")) == (
        "Tell owners and admins about any invoice where it has no matching purchase order, the currency is one of "
        "USD or EUR and the invoice is at least 1 day old.")
    assert sentence(params(rule("msme_supplier", "=", True), rule("bank_account_changed", "!=", False),
                           outcome="approval")) == (
        "Ask a person before paying any invoice where the supplier is a micro or small enterprise and its bank "
        "account changed.")
    m = custom(BIG_AND_NEW)
    parts = registry.template(KEY).readback(m.spec, m.params).parts
    assert [p.get("kind") for p in parts if "chip" in p] == ["choice", "condition", "condition"]


def test_the_gallery_example_must_be_replaced_and_is_locked_below_enterprise():
    assert registry.required_params(KEY) == ["name", "conditions"]
    business = next(t for t in registry.library(enterprise=False) if t["key"] == KEY)
    assert business["available"] is False and business["plan"] == "enterprise"
    assert business["group"] == base.GROUP_CUSTOM and business["order"] == 99
    assert business["readback"]["sentence"].startswith("Hold any invoice where the amount is above ₹5 lakh")
    fields = {f["key"]: f for f in business["editor"]["fields"]}
    assert set(fields) == set(cc.FIELDS)
    assert [o["op"] for o in fields["counterparty"]["ops"]] == ["=", "!=", "contains", "in"]
    assert [o["op"] for o in fields["new_supplier"]["ops"]] == ["=", "!="]
    assert [o["value"] for o in business["editor"]["outcomes"]] == ["hold", "approval", "notify"]
    assert next(t for t in registry.library(enterprise=True) if t["key"] == KEY)["available"] is True
    # Never recommended on first run, never offered to the drafting model below Enterprise.
    assert KEY not in registry.recommended(None)
    assert KEY not in {t.key for t in drafting._available(False)}
    assert KEY in {t.key for t in drafting._available(True)}
    assert "custom_condition" not in drafting.prompt_for("hold big invoices", enterprise=False)


def test_the_drafting_model_is_told_each_fact_and_its_written_amounts_become_micros():
    prompt = drafting.prompt_for("hold any invoice above 5 lakh from a new supplier", enterprise=True)
    assert "- custom_condition: \"Custom condition\"." in prompt
    assert ('      amount (Amount): money {"amount": "<number as written>", "currency": "<ISO 4217 code>"}; '
            "comparisons > >= < <= = !=") in prompt
    assert ('      confidence (How sure the facts are): one of the codes; with "in", a list of them (VERIFIED, '
            "CALIBRATED, EXTRACTED); comparisons = != in") in prompt
    assert '    - match: "all" or "any"; outcome: "hold", "approval" or "notify"' in prompt
    # The model writes the amount as the sentence does; Helm converts it, inside each condition too.
    answer = {"name": "Big first invoices", "conditions": [
        rule("amount", ">", {"amount": "5,00,000", "currency": "inr"}), rule("new_supplier", "=", True),
        rule("amount", "<", {"amount": "five", "currency": "INR"})]}
    converted = drafting._money_params(registry.template(KEY), answer)
    assert converted["conditions"][:2] == [rule("amount", ">", money(5 * LAKH)), rule("new_supplier", "=", True)]
    assert converted["conditions"][2] == answer["conditions"][2]  # not a number: left for the validator to say
    assert problems(params(*converted["conditions"][:2], name=converted["name"])) == []
    assert problems(params(*converted["conditions"], name=converted["name"]))[0].startswith("Condition 3, amount:")


def test_a_business_book_is_refused_in_words():
    assert problems(BIG_AND_NEW, enterprise=False) == ['"Custom condition" is part of the Enterprise plan.']
    assert problems(BIG_AND_NEW, enterprise=True) == []


def test_unknown_fields_wrong_operators_and_bad_values_are_refused_in_words():
    found = problems(params(rule("vendor_score", ">", 3), rule("amount", "contains", "5"),
                            rule("days_until_due", "<", "five"), rule("counterparty", "in", []),
                            rule("amount", ">", {"amount_micros": 5, "currency": "rupees"}),
                            rule("new_supplier", "=", "yes"), rule("confidence", "=", "SURE")))
    assert found[0].startswith('Condition 1: Helm cannot test "vendor_score". Choose one of: amount, currency, '
                               "supplier name, days until due")
    assert "MSME supplier (micro or small)" in found[0]
    assert found[1:] == [
        'Condition 2: the amount cannot be compared with "contains"; use "is above", "is at least", "is below", '
        '"is at most", "is" or "is not".',
        "Condition 3, days until due: write the number of days as a whole number between -3650 and 3650.",
        "Condition 4, supplier name: list one to 20 values, separated by commas.",
        "Condition 5, amount: write the amount and its currency, for example 500000 INR.",
        "Condition 6, new supplier (first invoice from them): choose yes or no.",
        "Condition 7, how sure the facts are: choose checked by a person, checked automatically or read by the "
        "model and not checked.",
    ]
    for p in found:
        assert "{" not in p and "Input should" not in p and "Value error" not in p, p


def test_settings_of_the_wrong_kind_are_refused_in_words_too():
    found = problems({"name": None, "conditions": "amount above 5 lakh", "match": 1, "outcome": None})
    assert found == [
        'Give the condition a short name, for example "Large invoices from new suppliers".',
        "List the conditions, one to 8 of them.",
        "Choose whether all the conditions must be true, or any one of them.",
        "Choose what Helm does: hold the invoice, ask a person before paying it, or tell you."]
    assert problems({"conditions": [rule("amount", ">", money(5 * LAKH))]}) == ["The name is needed."]
    assert problems({"name": "Big"}) == ["The list of conditions is needed."]


def test_the_rule_as_a_whole_is_checked_in_words():
    assert problems(params(name="  ")) == [
        'Give the condition a short name, for example "Large invoices from new suppliers".',
        "Add at least one condition."]
    assert problems(params(*[rule("days_until_due", "<", n) for n in range(9)])) == [
        "A custom condition has at most 8 conditions."]
    assert problems({**BIG_AND_NEW, "match": "most", "outcome": "pay"}) == [
        "Choose whether all the conditions must be true, or any one of them.",
        "Choose what Helm does: hold the invoice, ask a person before paying it, or tell you."]
    assert problems(params(rule("amount", ">", money(10 * LAKH)), rule("amount", "<", money(5 * LAKH)))) == [
        "The conditions on the amount contradict each other, so the rule would match no invoice."]
    assert problems(params(rule("days_until_due", ">=", 5), rule("days_until_due", "<", 5))) == [
        "The conditions on the days until it is due contradict each other, so the rule would match no invoice."]
    assert problems(params(rule("new_supplier", "=", True), rule("new_supplier", "!=", True))) == [
        "The conditions ask for a new supplier and its opposite at once, so the rule would match no invoice."]
    # With "any", or in different currencies, the same conditions are fine.
    assert problems(params(rule("amount", ">", money(10 * LAKH)), rule("amount", "<", money(5 * LAKH)),
                           match="any")) == []
    assert problems(params(rule("amount", ">", money(10 * LAKH)), rule("amount", "<", money(5 * M, "USD")))) == []
    assert problems(params(rule("held", "=", False))) == [
        "Whether an invoice is already held cannot decide whether to hold it: remove that condition, or choose to ask "
        "a person or to be told instead."]
    assert problems(params(rule("held", "=", True), outcome="notify")) == []


def test_values_are_stored_tidy_and_the_title_is_the_owners_name():
    m = custom(params(rule("currency", "in", [" usd", "USD", "eur "]), rule("counterparty", "=", "  Acme   Steel "),
                      name="  Foreign   bills "))
    assert m.params.name == "Foreign bills"
    assert m.spec.params["conditions"] == [rule("currency", "in", ["USD", "EUR"]),
                                           rule("counterparty", "=", "Acme Steel")]
    assert registry.template(KEY).title_from(m.params) == "Foreign bills"


# ---------------------------------------------------------------------------
# The evaluator
# ---------------------------------------------------------------------------


def _hits(p: dict, s: snap.Snapshot, promise_id: str):
    m = custom(p)
    return cc.Facts(s, m).matched(s.payable(promise_id))


def test_each_fact_reads_the_invoice_as_the_ledger_holds_it():
    first = payable(1, counterparty_key="name:acme", counterparty_name="Acme Steel", amount_micros=6 * LAKH,
                    received_on=TODAY - timedelta(days=30), invoice_date=TODAY - timedelta(days=31),
                    due_date=TODAY + timedelta(days=3))
    later = payable(2, counterparty_key="name:acme", counterparty_name="Acme Steel", amount_micros=6 * LAKH,
                    received_on=TODAY - timedelta(days=2), invoice_date=TODAY - timedelta(days=3),
                    confidence=v.CONFIDENCE_EXTRACTED, flags=(BANK,), has_matched_po=True, currency="INR")
    dollars = payable(3, counterparty_key=None, counterparty_name=None, amount_micros=9 * LAKH, currency="USD",
                      due_date=None, udyam_number="UDYAM-MH-00-0000001")
    s = full_book(payables=[first, later, dollars])
    big = params(rule("amount", ">", money(5 * LAKH)))
    assert _hits(big, s, "p1") and _hits(big, s, "p2")
    assert _hits(big, s, "p3") is None  # a USD invoice is never compared with a rupee amount
    new = params(rule("new_supplier", "=", True))
    assert _hits(new, s, "p1") and _hits(new, s, "p2") is None
    assert _hits(new, s, "p3")  # nobody Helm knows is new: the careful reading
    assert _hits(params(rule("days_until_due", "<=", 3)), s, "p1")
    assert _hits(params(rule("days_until_due", "<=", 3)), s, "p3") is None  # no due date: never satisfied
    assert _hits(params(rule("days_since_invoice", ">", 30)), s, "p1")
    assert _hits(params(rule("days_since_invoice", ">", 30)), s, "p2") is None
    assert _hits(params(rule("counterparty", "contains", "STEEL")), s, "p2")
    assert _hits(params(rule("counterparty", "in", ["acme steel", "Other"])), s, "p2")
    assert _hits(params(rule("counterparty", "!=", "Acme Steel")), s, "p3")
    assert _hits(params(rule("currency", "=", "usd")), s, "p3")
    assert _hits(params(rule("confidence", "in", ["EXTRACTED"])), s, "p2")
    assert _hits(params(rule("confidence", "!=", "EXTRACTED")), s, "p1")
    assert _hits(params(rule("bank_account_changed", "=", True)), s, "p2")
    assert _hits(params(rule("has_matched_po", "=", False)), s, "p1")
    assert _hits(params(rule("msme_supplier", "=", True)), s, "p3")
    assert _hits(params(rule("msme_supplier", "=", True)), s, "p1") is None
    # A confirmed category on the counterparty outranks the document: micro or small is, medium is not.
    def acme(category: str) -> snap.Snapshot:
        status = msme_rules.MsmeStatus(registered=True, category=category, confirmed=True,
                                       udyam_number="UDYAM-MH-00-0000002")
        cp = snap.Counterparty(key="name:acme", name="Acme Steel", role="SUPPLIER", msme=status)
        return full_book(payables=[first], counterparties={"name:acme": cp})

    assert _hits(params(rule("msme_supplier", "=", True)), acme(v.MSME_SMALL), "p1")
    assert _hits(params(rule("msme_supplier", "=", True)), acme(v.MSME_MEDIUM), "p1") is None
    assert _hits(params(rule("msme_supplier", "!=", True)), acme(v.MSME_MEDIUM), "p1")
    # "any" needs one; "all" needs every one, and names the ones that held.
    both = params(rule("amount", ">", money(5 * LAKH)), rule("new_supplier", "=", True), match="any")
    assert [r.field for r in _hits(both, s, "p2")] == ["amount"]
    assert _hits(BIG_AND_NEW, s, "p2") is None


def test_held_means_held_for_another_reason():
    own = custom(params(rule("days_until_due", ">", -100), outcome="notify"), n=7)
    mine = payable(1, settlement_state=v.SETTLEMENT_HELD, settlement_source=v.SOURCE_HELM,
                   hold_code=cc.code_of(own))
    other = payable(2, settlement_state=v.SETTLEMENT_HELD, settlement_source=v.SOURCE_PERSON, hold_code=None)
    s = full_book(payables=[mine, other])
    facts = cc.Facts(s, own)
    assert facts.value(mine, "held", TODAY) is False and facts.value(other, "held", TODAY) is True


# ---------------------------------------------------------------------------
# Outcomes: proposals, the Guard, the optimiser, the planner
# ---------------------------------------------------------------------------


def _book():
    big_new = payable(1, counterparty_key="name:newco", counterparty_name="NewCo", amount_micros=7 * LAKH,
                      received_on=TODAY - timedelta(days=1))
    small = payable(2, counterparty_key="name:newco2", counterparty_name="Small New", amount_micros=1 * LAKH)
    held = payable(3, counterparty_key="name:newco3", counterparty_name="Held New", amount_micros=8 * LAKH,
                   settlement_state=v.SETTLEMENT_HELD, settlement_source=v.SOURCE_PERSON)
    return full_book(payables=[big_new, small, held])


def test_hold_holds_a_matching_invoice_and_the_guard_refuses_paying_it():
    s, m = _book(), custom(BIG_AND_NEW)
    proposed = registry.template(KEY).propose(s, m)
    assert [(c.kind, c.promise_id) for c in proposed] == [(v.ACTION_HOLD_PAYMENT, "p1")]
    hold = proposed[0]
    assert hold.params["reason"] == f"CUSTOM_CONDITION:{m.id}"
    assert hold.title == "Hold INV-1: Large invoices from new suppliers"
    assert 'matches "Large invoices from new suppliers": the amount is above ₹5 lakh and the supplier is new' in \
        hold.explanation
    pay = guard(action(s.payable("p1"), m=custom(params(rule("days_until_due", ">", 99)), n=2)), s, [m],
                Actor(user_id="u", role="OWNER"), GuardContext())
    check = next(c for c in pay.checks if c.id == f"limit:{KEY}:{m.id}")
    assert not pay.passed and check.result == v.CHECK_FAIL
    assert check.title == 'Not caught by "Large invoices from new suppliers"'
    assert check.evidence["matched"] == ["the amount is above ₹5 lakh", "the supplier is new"]
    release = guard(action(s.payable("p1"), kind=v.ACTION_RELEASE_HOLD), s, [m], SYSTEM, GuardContext())
    assert not release.passed
    fine = guard(action(s.payable("p2")), s, [m], SYSTEM, GuardContext())
    assert next(c for c in fine.checks if c.id == f"limit:{KEY}:{m.id}").result == v.CHECK_PASS
    assert registry.template(KEY).optimizer(s, m).exclude == {
        "p1": 'matches "Large invoices from new suppliers"', "p3": 'matches "Large invoices from new suppliers"'}
    drift = registry.template(KEY).evaluate(s, m, {})
    assert drift.state == base.DRIFTING and drift.figure == "1 not held yet"


def test_approval_asks_a_person_and_never_refuses():
    s, m = _book(), custom({**BIG_AND_NEW, "outcome": "approval"})
    assert registry.template(KEY).propose(s, m) == []
    result = guard(action(s.payable("p1")), s, [m], SYSTEM, GuardContext())
    check = next(c for c in result.checks if c.id == f"limit:{KEY}:{m.id}")
    assert result.passed and result.needs_approval and check.result == v.CHECK_APPROVAL
    assert "so a person approves the payment" in check.detail
    assert registry.template(KEY).optimizer(s, m).exclude == {}
    # Releasing a person's hold is not a payment: the approval rule says nothing about it.
    assert not [c for c in guard(action(s.payable("p3"), kind=v.ACTION_RELEASE_HOLD), s, [m], SYSTEM,
                                 GuardContext()).checks if c.id.startswith("limit:")]


def test_notify_tells_owners_once_per_matching_invoice():
    s, m = _book(), custom({**BIG_AND_NEW, "outcome": "notify"})
    proposed = registry.template(KEY).propose(s, m)
    assert [(c.kind, c.promise_id) for c in proposed] == [(v.ACTION_NOTIFY, "p1"), (v.ACTION_NOTIFY, "p3")]
    assert proposed[0].title == "Large invoices from new suppliers: INV-1 from NewCo"
    again = registry.template(KEY).propose(s, m)
    assert [c.idempotency_key for c in again] == [c.idempotency_key for c in proposed]
    assert not [c for c in guard(action(s.payable("p1")), s, [m], SYSTEM, GuardContext()).checks
                if c.id.startswith("limit:")]


def test_a_hold_is_released_when_the_condition_stops_and_never_by_another_custom_mandate():
    big = custom(params(rule("amount", ">", money(5 * LAKH)), name="Big"), n=1)
    late = custom(params(rule("days_until_due", "<", 0), name="Overdue"), n=2)
    # Held by "Big", and it is no longer big (a credit note): "Big" releases it.
    shrunk = payable(1, amount_micros=1 * LAKH, settlement_state=v.SETTLEMENT_HELD,
                     settlement_source=v.SOURCE_HELM, hold_code=cc.code_of(big), settlement_version=2)
    # Held by "Big" and still big: neither "Big" nor "Overdue" (which it does not match) releases it.
    still = payable(2, amount_micros=9 * LAKH, settlement_state=v.SETTLEMENT_HELD, settlement_source=v.SOURCE_HELM,
                    hold_code=cc.code_of(big), due_date=TODAY + timedelta(days=9))
    composition = planner.compose(full_book(payables=[shrunk, still]), [big, late])
    releases = [p for p in composition.proposals if p.candidate.kind == v.ACTION_RELEASE_HOLD]
    assert [(p.candidate.promise_id, p.candidate.mandate.id) for p in releases] == [("p1", big.id)]
    assert "no longer matches the custom condition" in releases[0].candidate.explanation
    assert releases[0].result.passed
    # Changing the outcome to "notify" releases what the hold rule held.
    told = custom({**params(rule("amount", ">", money(5 * LAKH)), name="Big"), "outcome": "notify"}, n=1)
    composition = planner.compose(full_book(payables=[still]), [told])
    assert [p.candidate.promise_id for p in composition.proposals
            if p.candidate.kind == v.ACTION_RELEASE_HOLD] == ["p2"]


def test_the_planner_never_schedules_a_held_match_and_holds_it_instead():
    s = _book()
    composition = planner.compose(s, [custom(BIG_AND_NEW), mandate("pay_on_time", n=4)])
    kinds = {(p.candidate.kind, p.candidate.promise_id) for p in composition.proposals}
    assert (v.ACTION_HOLD_PAYMENT, "p1") in kinds
    assert (v.ACTION_SCHEDULE_PAYMENT, "p1") not in kinds
    assert (v.ACTION_SCHEDULE_PAYMENT, "p2") in kinds


def test_no_payment_that_matches_an_active_hold_condition_ever_passes():
    rng = random.Random(20261015 + 7)
    trials = caught = 0
    for _ in range(600):
        threshold = rng.choice([1, 3, 5]) * LAKH
        days = rng.choice([0, 5, 10])
        rules = [rule("amount", ">=", money(threshold)), rule("days_until_due", "<", days)]
        match = rng.choice(["all", "any"])
        m = custom(params(*rules, match=match), n=3)
        payables = [payable(rng.randint(0, 9999) * 11 + n, amount_micros=rng.choice([1, 2, 3, 5, 8]) * LAKH,
                            currency=rng.choice(["INR", "INR", "USD"]),
                            due_date=TODAY + timedelta(days=rng.randint(-5, 15))) for n in range(4)]
        s = full_book(payables=payables)
        for p in payables:
            result = guard(action(p, kind=rng.choice([v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT]),
                                  key=f"k{trials}"), s, [m], Actor(user_id="u", role="OWNER"), GuardContext())
            trials += 1
            big = p.currency == "INR" and p.amount_micros >= threshold
            soon = (p.due_date - TODAY).days < days
            if (big and soon) if match == "all" else (big or soon):
                caught += 1
                assert not result.passed and not result.can_run_alone, (match, threshold, days, p)
            else:
                assert next(c for c in result.checks if c.id.startswith("limit:")).result == v.CHECK_PASS
    assert trials == 2400 and caught > 600, caught


# ---------------------------------------------------------------------------
# The Time machine
# ---------------------------------------------------------------------------


def test_the_backtest_finds_what_matched_on_the_day_it_arrived():
    arrived = TODAY - timedelta(days=40)
    paid = payable(1, counterparty_key="name:newco", counterparty_name="NewCo", amount_micros=7 * LAKH,
                   received_on=arrived, invoice_date=arrived, due_date=arrived + timedelta(days=2),
                   settlement_state=v.SETTLEMENT_PAID, confidence=v.CONFIDENCE_VERIFIED)
    unchecked = payable(2, counterparty_key="name:other", counterparty_name="Other", amount_micros=6 * LAKH,
                        received_on=arrived, invoice_date=arrived, due_date=arrived + timedelta(days=30),
                        confidence=v.CONFIDENCE_EXTRACTED)
    history = History(start=TODAY - timedelta(days=90), end=TODAY, snapshot=full_book(payables=[paid, unchecked]))
    soon = params(rule("amount", ">", money(5 * LAKH)), rule("days_until_due", "<=", 2), name="Big and due soon")
    findings = registry.template(KEY).backtest(history, custom(soon))
    # Its due date is counted from the day it arrived (2 days), not from today (long past).
    assert [(f.on, f.honesty, f.category, f.value_micros) for f in findings] == [
        (arrived, v.HONESTY_CONFIRMED, base.CATEGORY_CUSTOM, 0)]
    assert findings[0].summary == (
        'inv-1.pdf (₹7 lakh) matched "Big and due soon": the amount is above ₹5 lakh and it is due in at most 2 '
        "days. It was paid. Helm would have held it.")
    loose = registry.template(KEY).backtest(history, custom({**params(rule("amount", ">", money(5 * LAKH))),
                                                            "outcome": "approval"}))
    assert [(f.honesty, f.action) for f in loose] == [(v.HONESTY_CONFIRMED, v.ACTION_APPROVE_PAYMENT),
                                                      (v.HONESTY_POTENTIAL, v.ACTION_APPROVE_PAYMENT)]
    assert loose[1].summary.endswith("Its facts were read by the model and not checked.")


def test_two_custom_mandates_never_share_a_guard_check_id():
    one, two = custom(BIG_AND_NEW, n=1), custom({**BIG_AND_NEW, "name": "Other"}, n=2)
    s = _book()
    ids = [c.id for c in guard(action(s.payable("p1")), s, [one, two], SYSTEM, GuardContext()).checks]
    assert len(ids) == len(set(ids)) and f"limit:{KEY}:{one.id}" in ids and f"limit:{KEY}:{two.id}" in ids


def test_mandate_ids_are_strings_in_hold_codes():
    m = registry.load(mandate_id=uuid.UUID(int=5), version=1, status=v.MANDATE_ACTIVE, title="x",
                      spec=registry.normalized(MandateSpec(template=KEY, kind="limit", params=BIG_AND_NEW)))
    assert cc.code_of(m) == f"CUSTOM_CONDITION:{uuid.UUID(int=5)}"
