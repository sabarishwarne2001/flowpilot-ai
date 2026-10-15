"""The payment-timing MILP (brief §6.1): known optima, infeasibility, the time limit, the greedy fallback."""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest

from app.services.helm import optimizer as opt
from app.services.helm import vocabulary as v

pytestmark = pytest.mark.no_db

M = 1_000_000  # one currency unit in micros
TODAY = date(2026, 10, 5)  # a Monday
RUN_DAYS = [date(2026, 10, 6), date(2026, 10, 9), date(2026, 10, 13), date(2026, 10, 16), date(2026, 10, 20),
            date(2026, 10, 23), date(2026, 10, 27), date(2026, 10, 30), date(2026, 11, 3)]
HORIZON_END = date(2026, 11, 4)


def problem(items, **kwargs):
    return opt.Problem(today=TODAY, run_days=RUN_DAYS, horizon_end=HORIZON_END, items=items, **kwargs)


def early_cost(amount: int, bps: int = 1200) -> int:
    return amount * bps // 10_000 // 365


def late_once(amount: int) -> int:
    """The "pay on time" mandate's one-off penalty: half a per cent of the invoice."""
    return amount // 200


def test_pays_on_the_last_run_day_before_the_due_date_when_nothing_else_matters():
    amount = 100_000 * M
    item = opt.Item(key="a", currency="INR", amount=amount, due=date(2026, 10, 22),
                    late_penalty_per_day=early_cost(amount, 2400), late_penalty_fixed=late_once(amount),
                    early_cost_per_day=early_cost(amount))
    solution = opt.solve(problem([item]))
    assert solution.status == v.SOLVER_OPTIMAL
    # Tuesday 20 Oct is the last run day on or before Thursday 22 Oct: paying earlier costs capital,
    # paying later costs the on-time penalty.
    assert solution.paid_on("a") == date(2026, 10, 20)


def test_takes_a_discount_worth_more_than_the_cost_of_capital():
    amount = 100_000 * M
    # 2/10 net 30 from 3 Oct: 2% (₹2,000) if paid by 13 Oct, due 2 Nov.
    item = opt.Item(key="d", currency="INR", amount=amount, due=date(2026, 11, 2),
                    discount_deadline=date(2026, 10, 13), discount_value=2_000 * M,
                    late_penalty_per_day=early_cost(amount, 2400), early_cost_per_day=early_cost(amount))
    solution = opt.solve(problem([item]))
    assert solution.paid_on("d") == date(2026, 10, 13)
    assert solution.discounts_micros == 2_000 * M


def test_skips_a_discount_worth_less_than_paying_early_costs():
    amount = 100_000 * M
    # A ₹50 discount for paying 20 days early costs ~₹657 of capital at 12% a year: not worth it.
    item = opt.Item(key="d", currency="INR", amount=amount, due=date(2026, 11, 2),
                    discount_deadline=date(2026, 10, 13), discount_value=50 * M,
                    late_penalty_per_day=early_cost(amount, 2400), late_penalty_fixed=late_once(amount),
                    early_cost_per_day=early_cost(amount))
    solution = opt.solve(problem([item]))
    assert solution.paid_on("d") == date(2026, 10, 30)
    assert solution.discounts_micros == 0


def test_hard_deadline_is_met_even_when_paying_early_costs_money():
    amount = 50_000 * M
    item = opt.Item(key="msme", currency="INR", amount=amount, due=date(2026, 11, 3),
                    hard_deadline=date(2026, 10, 15), deadline_kind="MSME", early_cost_per_day=early_cost(amount))
    solution = opt.solve(problem([item]))
    assert solution.status == v.SOLVER_OPTIMAL
    assert solution.paid_on("msme") == date(2026, 10, 13)


def test_cash_floor_moves_a_payment_until_the_money_arrives():
    big = 400_000 * M
    items = [opt.Item(key="x", currency="INR", amount=big, due=date(2026, 10, 9),
                      late_penalty_per_day=early_cost(big, 2400), early_cost_per_day=early_cost(big))]
    # ₹5 lakh cash, ₹2 lakh floor: only ₹3 lakh can go out until a ₹2 lakh receipt lands by 16 Oct.
    inflow = [0, 0, 0, 200_000 * M] + [200_000 * M] * 5
    solution = opt.solve(problem(items, cash={"INR": 500_000 * M}, inflows={"INR": inflow},
                                 floors={"INR": 200_000 * M}))
    assert solution.status == v.SOLVER_OPTIMAL
    assert solution.paid_on("x") == date(2026, 10, 16)


def test_each_currency_is_planned_on_its_own():
    items = [
        opt.Item(key="inr", currency="INR", amount=90 * M, due=date(2026, 10, 9), late_penalty_per_day=1 * M,
                 early_cost_per_day=M // 100),
        opt.Item(key="usd", currency="USD", amount=90 * M, due=date(2026, 10, 9), late_penalty_per_day=1 * M,
                 early_cost_per_day=M // 100),
    ]
    # Plenty of rupees, too few dollars: the dollar invoice waits, the rupee one does not.
    solution = opt.solve(problem(items, cash={"INR": 1_000 * M, "USD": 50 * M}, floors={"INR": 0, "USD": 0}))
    assert solution.paid_on("inr") == date(2026, 10, 9)
    assert solution.paid_on("usd") is None


def test_infeasible_floor_against_legal_deadlines_gives_a_plain_conflict_report():
    items = [
        opt.Item(key=f"msme-{n}", currency="INR", amount=300_000 * M, due=date(2026, 10, 16),
                 hard_deadline=date(2026, 10, 16), deadline_kind="MSME", label=f"MSME invoice {n}")
        for n in range(3)
    ]
    solution = opt.solve(problem(items, cash={"INR": 1_000_000 * M}, floors={"INR": 340_000 * M}))
    assert solution.status == v.SOLVER_CONFLICT
    report = solution.conflict
    assert report is not None
    assert "cash floor" in report.relaxed
    # ₹10 lakh − ₹9 lakh paid = ₹1 lakh left against a ₹3.4 lakh floor: ₹2.4 lakh short on 16 Oct.
    assert report.shortfalls[0].amount == 240_000 * M
    assert report.headline == ("Cash falls ₹2.4 lakh short of the floor on 16 Oct if every legal payment "
                               "deadline is met.")
    assert any(option.startswith("Lower the cash floor to ₹1 lakh") for option in report.options)
    assert any(option.startswith("Add ₹2.4 lakh of cash before 16 Oct") for option in report.options)
    # Every legal deadline is still met in the plan it returns.
    assert all(solution.paid_on(item.key) is not None for item in items)


def test_company_hard_deadlines_are_relaxed_before_the_cash_floor():
    items = [opt.Item(key="policy", currency="INR", amount=600_000 * M, due=date(2026, 10, 9),
                      hard_deadline=date(2026, 10, 9), deadline_kind="ON_TIME", late_penalty_per_day=10 * M,
                      label="Policy invoice")]
    inflow = [0, 0, 0, 400_000 * M] + [400_000 * M] * 5
    solution = opt.solve(problem(items, cash={"INR": 500_000 * M}, inflows={"INR": inflow},
                                 floors={"INR": 100_000 * M}))
    assert solution.status == v.SOLVER_CONFLICT
    assert solution.relaxed == ["pay_on_time hard deadlines"]
    assert solution.conflict and not solution.conflict.shortfalls
    assert solution.paid_on("policy") == date(2026, 10, 16)


def test_held_items_are_simply_not_variables_and_empty_problems_say_so():
    assert opt.solve(problem([])).status == v.SOLVER_EMPTY


def test_a_time_limit_falls_back_to_a_marked_approximate_plan(monkeypatch):
    items = [opt.Item(key=f"i{n}", currency="INR", amount=(1_000 + n) * M, due=RUN_DAYS[n % 9],
                      late_penalty_per_day=M, early_cost_per_day=M // 10) for n in range(40)]

    def slow(*args, **kwargs):
        return 1, None, []  # HiGHS: time limit reached, no solution

    monkeypatch.setattr(opt, "_solve_milp", slow)
    solution = opt.solve(problem(items, cash={"INR": 10_000_000 * M}, floors={"INR": 0}))
    assert solution.status == v.SOLVER_APPROXIMATE
    assert solution.solver == "greedy"
    assert all(solution.paid_on(item.key) is not None for item in items)


def test_greedy_respects_hard_deadlines_and_the_floor():
    items = [
        opt.Item(key="legal", currency="INR", amount=200 * M, due=date(2026, 10, 30),
                 hard_deadline=date(2026, 10, 14), deadline_kind="MSME"),
        opt.Item(key="other", currency="INR", amount=200 * M, due=date(2026, 10, 9), late_penalty_per_day=M),
    ]
    solution = opt.greedy(problem(items, cash={"INR": 300 * M}, floors={"INR": 0}))
    assert solution.paid_on("legal") == date(2026, 10, 13)
    assert solution.paid_on("other") is None  # only 300 available: the legal one goes first


def test_a_thousand_invoices_plan_inside_the_budget():
    rng_amounts = [((n * 7919) % 90_000 + 5_000) * M for n in range(1000)]
    items = [
        opt.Item(key=f"p{n}", currency="INR", amount=amount, due=TODAY + timedelta(days=(n * 13) % 35),
                 hard_deadline=(TODAY + timedelta(days=(n * 17) % 25)) if n % 7 == 0 else None,
                 deadline_kind="MSME" if n % 7 == 0 else None,
                 discount_deadline=(TODAY + timedelta(days=8)) if n % 5 == 0 else None,
                 discount_value=amount // 50 if n % 5 == 0 else 0,
                 late_penalty_per_day=early_cost(amount, 2400), early_cost_per_day=early_cost(amount))
        for n, amount in enumerate(rng_amounts)
    ]
    started = time.monotonic()
    solution = opt.solve(problem(items, cash={"INR": 30_000_000_000 * M}, floors={"INR": 1_000_000 * M}))
    elapsed = time.monotonic() - started
    assert solution.status in (v.SOLVER_OPTIMAL, v.SOLVER_APPROXIMATE)
    assert elapsed < 10, f"planning 1,000 payables took {elapsed:.1f}s"
