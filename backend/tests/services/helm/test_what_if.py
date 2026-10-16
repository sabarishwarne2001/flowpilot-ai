"""What-if on pure snapshots (no database): applying a scenario to a copy of a snapshot, refusing a
bad one in plain words, and comparing today's plan with the scenario's (`helm/what_if.py`)."""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from app.services.helm import msme as msme_rules
from app.services.helm import simulation
from app.services.helm import snapshot as snap
from app.services.helm import vocabulary as v
from app.services.helm import what_if
from app.services.helm.mandates import base
from tests.services.helm.test_guard import M, TODAY, mandate, payable
from tests.services.helm.test_library import WS, full_book

pytestmark = pytest.mark.no_db

P1 = str(uuid.UUID(int=101))
P2 = str(uuid.UUID(int=102))
R1 = str(uuid.UUID(int=201))
R2 = str(uuid.UUID(int=202))
CUSTOMER = "name:kesarbagh retail"


def _cash(**amounts: int) -> dict[str, snap.Cash]:
    return {c: snap.Cash(currency=c, amount_micros=a, as_of=TODAY, source=v.CASH_SOURCE_MANUAL, age_days=0)
            for c, a in amounts.items()}


def _receivable(promise_id: str, *, due_in: int, amount: int, paid: bool = False) -> snap.Receivable:
    return snap.Receivable(
        promise_id=promise_id, work_item_id=str(uuid.uuid5(uuid.NAMESPACE_URL, promise_id)), workspace_id=WS,
        document="s-1.pdf", document_number=f"S-{promise_id[-3:]}", counterparty_key=CUSTOMER,
        counterparty_name="Kesarbagh Retail", currency="INR", amount_micros=amount,
        invoice_date=TODAY - timedelta(days=20), due_date=TODAY + timedelta(days=due_in), status=v.STATUS_OPEN,
        paid_on=TODAY - timedelta(days=1) if paid else None, confidence=v.CONFIDENCE_CALIBRATED)


#: A customer who has always paid on the day (five observations: the simulation uses their history).
PUNCTUAL = {CUSTOMER: snap.Counterparty(
    key=CUSTOMER, name="Kesarbagh Retail", role="CUSTOMER",
    msme=msme_rules.MsmeStatus(registered=False, category=None, confirmed=False, udyam_number=None),
    behaviour={"days_late": [0, 0, 0, 0, 0]})}


def _book(**kw) -> snap.Snapshot:
    kw.setdefault("cash", _cash(INR=500_000 * M))
    kw.setdefault("payables", [
        payable(1, promise_id=P1, amount_micros=118_000 * M, due_date=TODAY + timedelta(days=10)),
        payable(2, promise_id=P2, amount_micros=60_000 * M, due_date=TODAY + timedelta(days=12))])
    return full_book(**kw)


FLOOR = {"floor": {"amount_micros": 100_000 * M, "currency": "INR"}}


def _mandates():
    return [mandate("pay_on_time", n=1), mandate("keep_cash_above", FLOOR, n=2)]


def _scenario(**kw) -> what_if.Scenario:
    scenario, problems = what_if.parse(kw)
    assert problems == []
    return scenario


# ---------------------------------------------------------------------------
# Applying a scenario to a copy
# ---------------------------------------------------------------------------


def test_a_cash_change_moves_only_todays_balance_in_its_currency_on_a_copy():
    book = _book(cash=_cash(INR=500_000 * M, USD=9_000 * M))
    scenario, problems = what_if.check(_scenario(cash_change_micros=-200_000 * M, currency="INR"), book)
    assert problems == []
    changed = what_if.apply(book, scenario)
    assert changed.cash["INR"].amount_micros == 300_000 * M
    assert changed.cash["USD"] == book.cash["USD"]
    assert book.cash["INR"].amount_micros == 500_000 * M  # the original is untouched
    assert changed is not book and changed.digest == book.digest  # same digest: the same seeded futures
    with pytest.raises(FrozenInstanceError):
        changed.cash["INR"].amount_micros = 0  # type: ignore[misc]


def test_a_delay_makes_open_customer_invoices_arrive_later_without_moving_their_due_dates():
    open_one = _receivable(R1, due_in=5, amount=200_000 * M)
    paid_one = _receivable(R2, due_in=-3, amount=50_000 * M, paid=True)
    book = _book(receivables=[open_one, paid_one], counterparties=PUNCTUAL)
    changed = what_if.apply(book, _scenario(delay_receivables_days=30))
    by_id = {r.promise_id: r for r in changed.receivables}
    assert by_id[R1].expected_delay_days == 30 and by_id[R1].due_date == open_one.due_date
    assert by_id[R2] == paid_one  # a paid invoice is not delayed
    assert book.receivables[0].expected_delay_days == 0

    # In the simulation the money arrives 30 days later: same seed, same futures, later inflow.
    before = simulation.simulate(book, seed=7, horizon_days=60)
    after = simulation.simulate(changed, seed=7, horizon_days=60)
    on = {p["on"]: p for p in before.cash_paths["INR"]}
    later = {p["on"]: p for p in after.cash_paths["INR"]}
    day10 = (TODAY + timedelta(days=10)).isoformat()
    day40 = (TODAY + timedelta(days=40)).isoformat()
    assert on[day10]["p50"] == 700_000 * M and later[day10]["p50"] == 500_000 * M
    assert later[day40]["p50"] == 700_000 * M


def test_a_hold_takes_the_invoice_out_of_the_plan_as_a_person_would():
    already = payable(3, promise_id=str(uuid.UUID(int=103)), settlement_state=v.SETTLEMENT_HELD,
                      settlement_source=v.SOURCE_HELM, hold_code="PROBABLE_DUPLICATE", hold_reason="Duplicate")
    book = _book(payables=[payable(1, promise_id=P1, settlement_state=v.SETTLEMENT_SCHEDULED,
                                   run_date=TODAY + timedelta(days=2)), payable(2, promise_id=P2), already])
    changed = what_if.apply(book, _scenario(hold_promise_ids=[P1, already.promise_id]))
    held = changed.payable(P1)
    assert (held.settlement_state, held.settlement_source, held.run_date) == (v.SETTLEMENT_HELD, v.SOURCE_PERSON,
                                                                             None)
    assert held.hold_reason == what_if.HOLD_REASON and not held.schedulable
    assert changed.payable(P2) == book.payable(P2)
    assert changed.payable(already.promise_id) == already  # Helm's own hold stays Helm's (it may release it)
    assert book.payable(P1).settlement_state == v.SETTLEMENT_SCHEDULED


# ---------------------------------------------------------------------------
# Refusing a bad scenario, in words
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw, words", [
    ({}, "Tell Helm what to change"),
    ({"cash_change_micros": 0, "delay_receivables_days": 0, "hold_promise_ids": []}, "Tell Helm what to change"),
    ({"delay_receivables_days": 91}, "Customers can be delayed by 0 to 90 days."),
    ({"delay_receivables_days": -1}, "Customers can be delayed by 0 to 90 days."),
    ({"cash_change_micros": 10**19}, "A cash change can be at most"),
    ({"cash_change_micros": 1.5}, "whole number of millionths"),
    ({"cash_change_micros": 5, "currency": "rupees"}, "three-letter code"),
    ({"delay_receivables_days": 5, "horizon_days": 3}, "Helm looks 7 to 90 days ahead"),
    ({"delay_receivables_days": 5, "horizon_days": 120}, "Helm looks 7 to 90 days ahead"),
    ({"hold_promise_ids": ["<script>"]}, "Invoice 1 of the list to hold is not an invoice you owe in this book."),
    ({"hold_promise_ids": [str(uuid.UUID(int=n)) for n in range(51)]}, "Hold at most 50 invoices"),
])
def test_a_bad_scenario_is_refused_in_plain_words(raw, words):
    _scenario_, problems = what_if.parse(raw)
    assert any(words in p for p in problems), problems
    assert not any("<script>" in p for p in problems)  # the input is never echoed


def test_holds_are_deduplicated_and_the_horizon_defaults_to_the_cockpits_ninety_days():
    scenario = _scenario(hold_promise_ids=[P1, P1.upper(), P2])
    assert scenario.hold_promise_ids == (P1, P2)
    assert scenario.horizon_days == 90


def test_the_book_decides_the_currency_and_which_invoices_can_be_held():
    one = _book()
    scenario, problems = what_if.check(_scenario(cash_change_micros=-5 * M), one)
    assert problems == [] and scenario.currency == "INR"  # the only currency the book holds

    two = _book(cash=_cash(INR=5 * M, USD=5 * M))
    _s, problems = what_if.check(_scenario(cash_change_micros=-5 * M), two)
    assert problems == ["This book holds cash in INR and USD; say which currency the change is in."]
    _s, problems = what_if.check(_scenario(cash_change_micros=-5 * M, currency="EUR"), two)
    assert problems == ["This book has no EUR cash position; it holds cash in INR and USD."]

    none = _book(cash={})
    _s, problems = what_if.check(_scenario(cash_change_micros=-5 * M), none)
    assert "does not know this book's cash yet" in problems[0]

    paid = _book(payables=[payable(1, promise_id=P1, settlement_state=v.SETTLEMENT_PAID)],
                 receivables=[_receivable(R1, due_in=5, amount=M)])
    _s, problems = what_if.check(_scenario(hold_promise_ids=[P1, R1, str(uuid.UUID(int=9))]), paid)
    assert problems == ["Invoice INV-1 is already paid, so holding it changes nothing.",
                        "Invoice 2 of the list to hold is not an invoice you owe in this book.",
                        "Invoice 3 of the list to hold is not an invoice you owe in this book."]


# ---------------------------------------------------------------------------
# Comparing today's plan with the scenario's
# ---------------------------------------------------------------------------


def test_a_cash_drop_lowers_the_chance_of_staying_above_the_floor_and_says_so():
    book = _book()
    scenario, _ = what_if.check(_scenario(cash_change_micros=-450_000 * M), book)
    result = what_if.simulate(book, _mandates(), scenario)
    assert result["baseline"]["floor_probability"] == {"INR": 1.0}
    assert result["scenario"]["floor_probability"] == {"INR": 0.0}
    assert result["floors"] == {"INR": 100_000 * M}
    assert result["baseline"]["cash_today"] == {"INR": 500_000 * M}
    assert result["scenario"]["cash_today"] == {"INR": 50_000 * M}
    assert len(result["scenario"]["cash_paths"]["INR"]) == 91  # today and the next 90 days
    changes = {c["template"]: c for c in result["mandates"]}
    assert changes["keep_cash_above"]["baseline"]["state"] == base.HOLDING
    assert changes["keep_cash_above"]["scenario"]["state"] == base.BREACHED
    assert result["scenario"]["conflict"] and not result["baseline"]["conflict"]
    summary = result["summary"]
    assert summary.startswith("If cash were ₹4.5 lakh lower today, INR cash would stay above the ₹1 lakh floor "
                              "in 0 of 100 simulated futures over the next 90 days, down from 100 as things stand.")
    assert "Keep cash above a floor would go from holding to breached." in summary
    assert "The plan would then have a conflict:" in summary
    assert result["saved"] is False and result["seed"] == str(simulation.seed_for(book.digest))


def test_holding_an_invoice_removes_its_payment_and_an_unchanged_mandate_is_not_listed():
    book = _book()
    result = what_if.simulate(book, _mandates(), _scenario(hold_promise_ids=[P1], horizon_days=30))
    assert result["baseline"]["payments"] == 2 and result["scenario"]["payments"] == 1
    assert result["baseline"]["payments_by_currency"] == {"INR": 178_000 * M}
    assert result["scenario"]["payments_by_currency"] == {"INR": 60_000 * M}
    assert result["mandates"] == []  # holding a payment breaks no mandate here
    assert "1 invoice was held (INV-1)" in result["summary"]
    assert "No mandate changes state." in result["summary"]
    assert "The plan would schedule 1 payment instead of 2." in result["summary"]
    # Holding money back raises the cautious low point.
    assert result["scenario"]["lowest"]["INR"]["p10"] > result["baseline"]["lowest"]["INR"]["p10"]
    assert len(result["baseline"]["cash_paths"]["INR"]) == 31


def test_customers_paying_later_shows_in_the_path_and_a_book_without_receivables_says_so():
    book = _book(receivables=[_receivable(R1, due_in=3, amount=300_000 * M)], counterparties=PUNCTUAL,
                 cash=_cash(INR=150_000 * M))
    result = what_if.simulate(book, _mandates(), _scenario(delay_receivables_days=45, horizon_days=30))
    base_path = {p["on"]: p["p50"] for p in result["baseline"]["cash_paths"]["INR"]}
    what_path = {p["on"]: p["p50"] for p in result["scenario"]["cash_paths"]["INR"]}
    day5 = (TODAY + timedelta(days=5)).isoformat()
    assert what_path[day5] < base_path[day5]  # the 3 lakh is no longer in by day 5
    assert result["summary"].startswith("If customers paid 45 days later than usual,")

    empty = what_if.simulate(_book(), _mandates(), _scenario(delay_receivables_days=10))
    assert "No customer invoice is open, so paying later changes nothing yet." in empty["summary"]
    assert empty["baseline"]["cash_paths"] == empty["scenario"]["cash_paths"]


def test_without_a_floor_the_lowest_point_is_compared_and_without_cash_helm_says_it_cannot_draw():
    book = _book()
    result = what_if.simulate(book, [mandate("pay_on_time")], _scenario(cash_change_micros=-100_000 * M,
                                                                        currency="INR"))
    assert result["baseline"]["floor_probability"] == {} and result["floors"] == {}
    assert "INR cash's lowest cautious point over the next 90 days would be" in result["summary"]
    assert "as things stand." in result["summary"]

    blind = what_if.simulate(_book(cash={}), [mandate("pay_on_time")], _scenario(hold_promise_ids=[P1]))
    assert "it does not know this book's cash" in blind["summary"]
    assert blind["baseline"]["cash_paths"] == {}


def test_a_reader_who_cannot_read_the_workspace_sees_states_but_not_details():
    book = _book()
    scenario, _ = what_if.check(_scenario(cash_change_micros=-450_000 * M), book)
    result = what_if.simulate(book, _mandates(), scenario, readable=False)
    changes = {c["template"]: c for c in result["mandates"]}
    assert all(c["baseline"]["detail"] == what_if.MASK and c["scenario"]["detail"] == what_if.MASK
               for c in changes.values())
    assert changes["keep_cash_above"]["scenario"]["state"] == base.BREACHED
    assert result["scenario"]["conflict"]["options"] == [] and result["scenario"]["conflict"]["headline"]
    assert result["readable"] is False
