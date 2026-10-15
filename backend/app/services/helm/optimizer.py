"""Payment timing as a mixed-integer programme (`scipy.optimize.milp`, the HiGHS solver).

THE PROGRAMME
=============

Variables: `x[i, d] = 1` pays invoice *i* on payment-run day *d* (inside the horizon); an invoice
with every `x[i, ·] = 0` is deferred past the horizon.

    maximise   Σ x[i,d] · (discount(i,d) − late(i,d) − early(i,d) − defer(i))
    subject to Σ_d x[i,d] ≤ 1                                   (paid at most once)
               Σ_{d ≤ H_i} x[i,d] = 1        for a hard deadline H_i inside the horizon
               opening_c + inflow_c(d_k) − Σ_{i∈c, d ≤ d_k} amount_i · x[i,d] ≥ floor_c
                                                for every currency c and run day d_k

`discount` is the early-payment discount when paid by its deadline (only when the discount mandate
is active and the discount is worth its yearly rate); `late` the late penalty per day after the
due date (the "pay on time" mandate's rate, or a stated late fee); `early` the cost of paying before
the due date at the cost of capital; `defer` the penalty of leaving it unpaid at the horizon's end.
Held, disputed and paid invoices are never variables. Amounts enter in currency units (micros /
10⁶) so the solver works with well-scaled numbers.

WHEN IT CANNOT
==============

* **Time limit** (default 5 s): HiGHS returns its best solution so far (marked APPROXIMATE); with
  none, the greedy heuristic below plans instead (also APPROXIMATE).
* **Infeasible:** soft goals are relaxed in priority order — first the "pay on time" deadlines a
  company set as hard, then the cash floor through a shortfall variable with a large penalty — and
  `ConflictReport` says, in plain words, what falls short, when, and the options.

GREEDY HEURISTIC (documented fallback)
======================================

Invoices are taken in order: hard deadline first (earliest first), then discount value per unit of
money (highest first), then due date. Each is put on the latest run day that is on or before its
target (hard deadline, discount deadline when the discount is worth taking, else the due date)
and keeps every later day's cash at or above the floor; failing that, the earliest feasible later
day; failing that, it is deferred. Deterministic: ties break on the item key.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional, Sequence

import numpy as np

from app.services.helm import money
from app.services.helm import vocabulary as v

logger = logging.getLogger("app.services.helm.optimizer")

UNIT = float(money.MICROS)
SHORTFALL_PENALTY = 1_000.0  # per currency unit of shortfall: dwarfs any discount or penalty
DEFAULT_TIME_LIMIT_S = 5.0


@dataclass(frozen=True)
class Item:
    key: str
    currency: str
    amount: int
    due: date
    hard_deadline: Optional[date] = None
    #: "MSME" (the law) or "ON_TIME" (the company's own hard "pay on time") — relaxed in that order: ON_TIME first.
    deadline_kind: Optional[str] = None
    discount_deadline: Optional[date] = None
    discount_value: int = 0
    late_penalty_per_day: int = 0
    #: Charged once when the invoice is paid after its due date (or left unpaid past it): a late
    #: payment is a broken promise however few days late, so a day late never beats a few days early.
    late_penalty_fixed: int = 0
    early_cost_per_day: int = 0
    label: str = ""


@dataclass(frozen=True)
class Problem:
    today: date
    run_days: Sequence[date]
    horizon_end: date
    items: Sequence[Item]
    #: Opening cash per currency (micros). A currency with no position has no cash constraint.
    cash: dict[str, int] = field(default_factory=dict)
    #: Cumulative conservative net inflow by run day, per currency (micros), aligned with run_days.
    inflows: dict[str, Sequence[int]] = field(default_factory=dict)
    floors: dict[str, int] = field(default_factory=dict)
    time_limit_s: float = DEFAULT_TIME_LIMIT_S


@dataclass
class Shortfall:
    currency: str
    on: date
    amount: int


@dataclass
class ConflictReport:
    headline: str
    shortfalls: list[Shortfall]
    relaxed: list[str]
    options: list[str]
    late_items: list[str]

    def as_json(self) -> dict:
        return {
            "headline": self.headline,
            "shortfalls": [{"currency": s.currency, "on": s.on.isoformat(), "amount_micros": s.amount}
                           for s in self.shortfalls],
            "relaxed": list(self.relaxed),
            "options": list(self.options),
            "late_items": list(self.late_items),
        }


@dataclass
class Solution:
    status: str
    schedule: dict[str, Optional[date]]
    solver: str
    objective_micros: int = 0
    discounts_micros: int = 0
    late_penalties_micros: int = 0
    early_cost_micros: int = 0
    conflict: Optional[ConflictReport] = None
    relaxed: list[str] = field(default_factory=list)
    duration_ms: int = 0

    def paid_on(self, key: str) -> Optional[date]:
        return self.schedule.get(key)


def _value(item: Item, day: date) -> int:
    worth = 0
    if item.discount_value and item.discount_deadline and day <= item.discount_deadline:
        worth += item.discount_value
    late_days = (day - item.due).days
    if late_days > 0:
        worth -= item.late_penalty_fixed + item.late_penalty_per_day * late_days
    elif late_days < 0:
        worth -= item.early_cost_per_day * (-late_days)
    return worth


def _defer_value(item: Item, horizon_end: date) -> int:
    late_days = (horizon_end + timedelta(days=1) - item.due).days
    return -(item.late_penalty_fixed + item.late_penalty_per_day * late_days) if late_days > 0 else 0


def effective_deadline(item: Item, run_days: Sequence[date]) -> Optional[date]:
    """The last run day the deadline allows; the first run day when it is already too late."""
    if item.hard_deadline is None or not run_days:
        return None
    allowed = [d for d in run_days if d <= item.hard_deadline]
    return allowed[-1] if allowed else run_days[0]


def _breakdown(problem: Problem, schedule: dict[str, Optional[date]]) -> tuple[int, int, int, int]:
    discounts = late = early = 0
    for item in problem.items:
        day = schedule.get(item.key)
        if day is None:
            late += -_defer_value(item, problem.horizon_end)
            continue
        if item.discount_value and item.discount_deadline and day <= item.discount_deadline:
            discounts += item.discount_value
        delta = (day - item.due).days
        if delta > 0:
            late += item.late_penalty_fixed + item.late_penalty_per_day * delta
        elif delta < 0:
            early += item.early_cost_per_day * (-delta)
    return discounts - late - early, discounts, late, early


def _cash_rows(problem: Problem, index: dict[tuple[int, int], int]):
    """One row per (currency with a position, run day): Σ paid by that day ≤ opening + inflow − floor.

    Returned as sparse triplets (row, column, value), the bounds and a label per row."""
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    upper: list[float] = []
    labels: list[tuple[str, date]] = []
    for currency in sorted(problem.cash):
        opening = problem.cash[currency]
        floor = problem.floors.get(currency, 0)
        inflow = list(problem.inflows.get(currency) or [0] * len(problem.run_days))
        members = [i for i, item in enumerate(problem.items) if item.currency == currency]
        for k, _day in enumerate(problem.run_days):
            r = len(upper)
            for i in members:
                amount = problem.items[i].amount / UNIT
                for j in range(k + 1):
                    rows.append(r)
                    cols.append(index[(i, j)])
                    vals.append(amount)
            upper.append((opening + int(inflow[k]) - floor) / UNIT)
            labels.append((currency, problem.run_days[k]))
    return rows, cols, vals, upper, labels


def _solve_milp(problem: Problem, *, hard_kinds: set[str], soft_cash: bool, time_limit: float):
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_matrix

    items, days = problem.items, list(problem.run_days)
    index: dict[tuple[int, int], int] = {}
    for i, _item in enumerate(items):
        for j, _day in enumerate(days):
            index[(i, j)] = len(index)
    n_x = len(index)
    c_rows, c_cols, c_vals, cash_upper, cash_labels = _cash_rows(problem, index)
    n_cash = len(cash_upper)
    n_s = n_cash if soft_cash else 0
    n_vars = n_x + n_s

    c = np.zeros(n_vars)
    for (i, j), col in index.items():
        item = items[i]
        c[col] = -(_value(item, days[j]) - _defer_value(item, problem.horizon_end)) / UNIT
    for s in range(n_s):
        c[n_x + s] = SHORTFALL_PENALTY

    constraints = []
    if items:
        once_rows = [i for (i, _j) in index]
        once_cols = list(index.values())
        once = coo_matrix((np.ones(len(once_cols)), (once_rows, once_cols)), shape=(len(items), n_vars)).tocsr()
        constraints.append(LinearConstraint(once, -np.inf, 1))

    h_rows: list[int] = []
    h_cols: list[int] = []
    for i, item in enumerate(items):
        if item.hard_deadline is None or (item.deadline_kind or "") not in hard_kinds:
            continue
        limit = effective_deadline(item, days)
        if limit is None:
            continue
        r = len(set(h_rows))
        for j, day in enumerate(days):
            if day <= limit:
                h_rows.append(r)
                h_cols.append(index[(i, j)])
    if h_rows:
        n_hard = max(h_rows) + 1
        hard = coo_matrix((np.ones(len(h_cols)), (h_rows, h_cols)), shape=(n_hard, n_vars)).tocsr()
        constraints.append(LinearConstraint(hard, np.ones(n_hard), np.inf))

    if n_cash:
        rows, cols, vals = list(c_rows), list(c_cols), list(c_vals)
        if soft_cash:
            for s in range(n_s):
                rows.append(s)
                cols.append(n_x + s)
                vals.append(-1.0)  # paid − shortfall ≤ available
        matrix = coo_matrix((np.array(vals), (rows, cols)), shape=(n_cash, n_vars)).tocsr()
        constraints.append(LinearConstraint(matrix, -np.inf, np.array(cash_upper)))

    integrality = np.concatenate([np.ones(n_x), np.zeros(n_s)])
    lower = np.zeros(n_vars)
    upper = np.concatenate([np.ones(n_x), np.full(n_s, np.inf)])
    result = milp(c=c, constraints=constraints, integrality=integrality, bounds=Bounds(lower, upper),
                  options={"time_limit": time_limit, "disp": False, "mip_rel_gap": 1e-6})
    schedule: Optional[dict[str, Optional[date]]] = None
    shortfalls: list[Shortfall] = []
    if result.x is not None:
        schedule = {item.key: None for item in items}
        for (i, j), col in index.items():
            if result.x[col] > 0.5:
                schedule[items[i].key] = days[j]
        if soft_cash:
            for s, (currency, day) in enumerate(cash_labels):
                amount = int(round(result.x[n_x + s] * UNIT))
                if amount > money.MICROS // 100:
                    shortfalls.append(Shortfall(currency=currency, on=day, amount=amount))
    return result.status, schedule, shortfalls


def _greedy(problem: Problem) -> dict[str, Optional[date]]:
    days = list(problem.run_days)
    paid_by_day: dict[str, list[int]] = {c: [0] * len(days) for c in problem.cash}

    def fits(item: Item, k: int) -> bool:
        if item.currency not in problem.cash:
            return True
        opening = problem.cash[item.currency]
        floor = problem.floors.get(item.currency, 0)
        inflow = list(problem.inflows.get(item.currency) or [0] * len(days))
        cumulative = 0
        for j in range(len(days)):
            cumulative += paid_by_day[item.currency][j] + (item.amount if j == k else 0)
            if j >= k and opening + inflow[j] - cumulative < floor:
                return False
        return True

    def order(item: Item):
        hard = effective_deadline(item, days) or date.max
        per_unit = -(item.discount_value / item.amount) if item.amount else 0
        return (hard, per_unit, item.due, item.key)

    schedule: dict[str, Optional[date]] = {}
    for item in sorted(problem.items, key=order):
        target = effective_deadline(item, days)
        if target is None:
            target = item.discount_deadline if (item.discount_value and item.discount_deadline) else item.due
        on_or_before = [k for k, d in enumerate(days) if d <= target]
        later = [k for k, d in enumerate(days) if d > target]
        choice = None
        for k in list(reversed(on_or_before)) + later:
            if _value(item, days[k]) - _defer_value(item, problem.horizon_end) < 0 and item.hard_deadline is None:
                continue  # paying here is worth less than waiting past the horizon
            if fits(item, k):
                choice = k
                break
        if choice is None:
            schedule[item.key] = None
            continue
        schedule[item.key] = days[choice]
        if item.currency in paid_by_day:
            paid_by_day[item.currency][choice] += item.amount
    return schedule


def _report(problem: Problem, schedule: dict[str, Optional[date]], shortfalls: list[Shortfall],
            relaxed: list[str]) -> ConflictReport:
    by_key = {item.key: item for item in problem.items}
    late = [key for key, day in schedule.items()
            if by_key[key].hard_deadline and (day is None or day > (effective_deadline(by_key[key],
                                                                                       problem.run_days) or day))]
    options: list[str] = []
    if shortfalls:
        worst = max(shortfalls, key=lambda s: s.amount)
        floor = problem.floors.get(worst.currency, 0)
        headline = (f"Cash falls {money.say(worst.amount, worst.currency)} short of the floor on "
                    f"{worst.on.strftime('%d %b')} if every legal payment deadline is met.")
        soft_paid = sorted(
            (item for item in problem.items
             if item.currency == worst.currency and item.deadline_kind != "MSME"
             and schedule.get(item.key) is not None and schedule[item.key] <= worst.on),
            key=lambda item: -item.amount)
        if soft_paid:
            names = ", ".join(item.label or item.key for item in soft_paid[:3])
            options.append(f"Delay {'these' if len(soft_paid) > 1 else 'this'} {min(len(soft_paid), 3)} "
                           f"non-urgent invoice{'s' if len(soft_paid) > 1 else ''}: {names}.")
        options.append(f"Lower the cash floor to {money.say(max(0, floor - worst.amount), worst.currency)} "
                       f"for that week.")
        options.append(f"Add {money.say(worst.amount, worst.currency)} of cash before "
                       f"{worst.on.strftime('%d %b')}.")
    else:
        headline = (f"{len(late)} invoice{'s' if len(late) != 1 else ''} cannot be paid by the deadline "
                    "your own \"pay on time\" mandate sets, without breaking the cash floor.")
        options.append("Accept the later payment dates shown in the plan.")
        options.append("Lower the cash floor, or add cash, to pay them on time.")
    return ConflictReport(headline=headline, shortfalls=shortfalls, relaxed=relaxed, options=options,
                          late_items=sorted(late))


def solve(problem: Problem) -> Solution:
    started = time.monotonic()
    if not problem.items or not problem.run_days:
        return Solution(status=v.SOLVER_EMPTY, schedule={item.key: None for item in problem.items},
                        solver="none", duration_ms=int((time.monotonic() - started) * 1000))
    deadline = started + problem.time_limit_s

    def remaining() -> float:
        return max(0.5, deadline - time.monotonic())

    attempts = [
        ({"MSME", "ON_TIME"}, False, []),
        ({"MSME"}, False, ["pay_on_time hard deadlines"]),
        ({"MSME"}, True, ["pay_on_time hard deadlines", "cash floor"]),
    ]
    for hard, soft_cash, relaxed in attempts:
        try:
            status, schedule, shortfalls = _solve_milp(problem, hard_kinds=hard, soft_cash=soft_cash,
                                                       time_limit=remaining())
        except Exception:  # noqa: BLE001 - a solver failure plans greedily, never crashes the plan
            logger.exception("helm.optimizer_failed")
            status, schedule, shortfalls = 4, None, []
        if status == 2:  # infeasible: relax the next soft goal
            continue
        if schedule is None:
            break  # time limit with nothing found, or a solver error: greedy below
        objective, discounts, late, early = _breakdown(problem, schedule)
        conflict = _report(problem, schedule, shortfalls, relaxed) if relaxed else None
        if status == 0 and not relaxed:
            outcome = v.SOLVER_OPTIMAL
        elif relaxed:
            outcome = v.SOLVER_CONFLICT
        else:
            outcome = v.SOLVER_APPROXIMATE
        return Solution(status=outcome, schedule=schedule, solver="highs", objective_micros=objective,
                        discounts_micros=discounts, late_penalties_micros=late, early_cost_micros=early,
                        conflict=conflict, relaxed=relaxed,
                        duration_ms=int((time.monotonic() - started) * 1000))

    schedule = _greedy(problem)
    objective, discounts, late, early = _breakdown(problem, schedule)
    return Solution(status=v.SOLVER_APPROXIMATE, schedule=schedule, solver="greedy", objective_micros=objective,
                    discounts_micros=discounts, late_penalties_micros=late, early_cost_micros=early,
                    duration_ms=int((time.monotonic() - started) * 1000))


def greedy(problem: Problem) -> Solution:
    """The fallback on its own (tests, and the backtest's day-by-day replay)."""
    schedule = _greedy(problem)
    objective, discounts, late, early = _breakdown(problem, schedule)
    return Solution(status=v.SOLVER_APPROXIMATE, schedule=schedule, solver="greedy", objective_micros=objective,
                    discounts_micros=discounts, late_penalties_micros=late, early_cost_micros=early)


__all__ = ["ConflictReport", "Item", "Problem", "Shortfall", "Solution", "effective_deadline", "greedy", "solve"]
