"""The simulation: 1,000 seeded futures of the book's cash, per currency (NumPy, vectorised).

HOW A FUTURE IS BUILT
=====================

* **Customers pay late the way they have before.** Each open receivable's delay is drawn from that
  customer's own history of days late (a bootstrap of `behaviour.days_late`). With fewer than five
  observations Helm uses a cautious default instead: a quarter on time, and the rest 15, 30, 45 or 60
  days late, with one in ten not paying inside the horizon at all. A what-if may add a fixed number
  of days on top (`Receivable.expected_delay_days`: "customers pay 15 days later than usual").
* **Recurring suppliers keep invoicing.** A supplier with a regular cadence (at least three
  invoices) is expected to invoice again: the next dates step by its usual gap, with the gap's own
  spread as noise, at its usual amount (±15%), due after its usual terms. Those are outflows the
  ledger does not know yet.
* **The plan's payments** go out on their run days, the same in every future.

OUTPUTS
=======

P10, P50 and P90 cash per currency per day, the probability each cash floor holds over the horizon
(the share of futures whose lowest point stays at or above it), the cautious (P10) cumulative net
inflow the optimiser plans against, and the expected-next-invoice list. A usual invoice that has not
arrived is itself a drift signal.

Same inputs and the same seed give the same numbers; the seed is stored with the plan.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional, Sequence

import numpy as np

from app.services.helm import money
from app.services.helm.snapshot import Snapshot

FUTURES = 1000
MIN_OBSERVATIONS = 5
#: Days late, and how likely, when a customer has too little history (cautious on purpose).
DEFAULT_DELAYS = np.array([0, 15, 30, 45, 60, 10_000])
DEFAULT_WEIGHTS = np.array([0.25, 0.25, 0.20, 0.10, 0.10, 0.10])
AMOUNT_NOISE = 0.15


@dataclass
class SimulationResult:
    seed: int
    days: list[date]
    #: currency -> [{"on": iso, "p10": micros, "p50": micros, "p90": micros}]
    cash_paths: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: currency -> probability (0..1) the floor holds every day of the horizon
    floor_probability: dict[str, float] = field(default_factory=dict)
    #: currency -> cumulative cautious net inflow (micros) by each day (aligned with `days`)
    p10_inflow: dict[str, list[int]] = field(default_factory=dict)
    expected_invoices: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0

    def p10_inflow_by(self, currency: str, on: date) -> int:
        series = self.p10_inflow.get(currency)
        if not series or not self.days:
            return 0
        index = min(max(0, (on - self.days[0]).days), len(series) - 1)
        return int(series[index])

    def as_json(self) -> dict[str, Any]:
        return {"seed": self.seed, "futures": FUTURES, "cash_paths": self.cash_paths,
                "floor_probability": self.floor_probability, "expected_invoices": self.expected_invoices,
                "duration_ms": self.duration_ms}


def seed_for(digest: str) -> int:
    """A stable 63-bit seed from the plan's inputs digest."""
    return int(digest[:15], 16) if digest else 0


def _day_index(as_of: date, on: Optional[date], horizon: int) -> int:
    if on is None:
        return horizon + 1
    return max(0, (on - as_of).days)


def simulate(snapshot: Snapshot, *, seed: int, horizon_days: Optional[int] = None,
             planned: Optional[dict[str, Sequence[tuple[date, int]]]] = None,
             floors: Optional[dict[str, int]] = None, futures: int = FUTURES) -> SimulationResult:
    started = time.monotonic()
    horizon = int(horizon_days or snapshot.horizon_days)
    as_of = snapshot.as_of
    days = [as_of + timedelta(days=d) for d in range(horizon + 1)]
    n_days = len(days)
    rng = np.random.default_rng(seed)
    currencies = snapshot.currencies()
    flows = {c: np.zeros((futures, n_days)) for c in currencies}
    rows = np.arange(futures)

    # Customers: bootstrap each one's own delays (or the cautious default).
    by_customer: dict[str, list[Any]] = {}
    for receivable in snapshot.receivables:
        if receivable.open and receivable.currency in flows:
            by_customer.setdefault(receivable.counterparty_key or receivable.promise_id, []).append(receivable)
    for key in sorted(by_customer):
        group = sorted(by_customer[key], key=lambda r: r.promise_id)
        behaviour = (snapshot.counterparties.get(key).behaviour if key in snapshot.counterparties else {}) or {}
        history = [int(d) for d in (behaviour.get("days_late") or [])]
        if len(history) >= MIN_OBSERVATIONS:
            delays = rng.choice(np.array(history), size=(futures, len(group)))
        else:
            delays = rng.choice(DEFAULT_DELAYS, size=(futures, len(group)), p=DEFAULT_WEIGHTS)
        for column, receivable in enumerate(group):
            base_index = _day_index(as_of, receivable.due_date or as_of, horizon)
            index = base_index + delays[:, column] + int(receivable.expected_delay_days or 0)
            index = np.maximum(index, 0)
            inside = index < n_days
            np.add.at(flows[receivable.currency], (rows[inside], index[inside]), float(receivable.amount_micros))

    # Recurring suppliers: their next invoices, which the ledger cannot know yet.
    expected: list[dict[str, Any]] = []
    known_open = {p.counterparty_key for p in snapshot.payables if p.open}
    for key in sorted(snapshot.counterparties):
        cp = snapshot.counterparties[key]
        behaviour = cp.behaviour or {}
        cadence = behaviour.get("cadence_days")
        typical = behaviour.get("typical_amount_micros")
        last = behaviour.get("last_invoice_on")
        currency = behaviour.get("currency")
        if not cadence or not typical or not last or currency not in flows or cp.role == "CUSTOMER":
            continue
        last_on = date.fromisoformat(str(last)[:10]) if not isinstance(last, date) else last
        spread = int(behaviour.get("cadence_spread_days") or 0)
        terms_days = int(behaviour.get("terms_days") or 30)
        next_on = last_on + timedelta(days=int(cadence))
        expected.append({"counterparty_key": key, "counterparty": cp.name, "expected_on": next_on.isoformat(),
                         "currency": currency, "amount_micros": int(typical),
                         "missing": bool(behaviour.get("usual_invoice_missing"))})
        steps = max(1, horizon // max(1, int(cadence)) + 2)
        jitter = rng.integers(-spread, spread + 1, size=(futures, steps)) if spread else np.zeros((futures, steps), int)
        amounts = typical * np.clip(rng.normal(1.0, AMOUNT_NOISE, size=(futures, steps)), 0.5, 1.5)
        for step in range(steps):
            invoice_index = _day_index(as_of, last_on + timedelta(days=int(cadence) * (step + 1)), horizon)
            if key in known_open and step == 0:
                continue  # its next invoice is probably the one already open in the ledger
            due_index = invoice_index + terms_days + jitter[:, step]
            due_index = np.maximum(due_index, 0)
            inside = due_index < n_days
            np.add.at(flows[currency], (rows[inside], due_index[inside]), -amounts[inside, step])

    result = SimulationResult(seed=seed, days=days, expected_invoices=expected)
    for currency in currencies:
        cumulative_inflow = np.cumsum(flows[currency], axis=1)
        result.p10_inflow[currency] = [int(x) for x in np.percentile(cumulative_inflow, 10, axis=0)]
        cash = snapshot.cash.get(currency)
        if cash is None:
            continue
        outflow = np.zeros(n_days)
        for on, amount in (planned or {}).get(currency, []):
            index = _day_index(as_of, on, horizon)
            if index < n_days:
                outflow[index] += amount
        paths = cash.amount_micros + cumulative_inflow - np.cumsum(outflow)[None, :]
        p10, p50, p90 = np.percentile(paths, [10, 50, 90], axis=0)
        result.cash_paths[currency] = [
            {"on": days[d].isoformat(), "p10": int(p10[d]), "p50": int(p50[d]), "p90": int(p90[d])}
            for d in range(n_days)
        ]
        floor = (floors or {}).get(currency)
        if floor is not None:
            result.floor_probability[currency] = round(float(np.mean(paths.min(axis=1) >= floor)), 4)
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


def amounts_say(path: list[dict[str, Any]], currency: str) -> str:
    low = min(path, key=lambda p: p["p10"]) if path else None
    return f"lowest cautious point {money.say(low['p10'], currency)} on {low['on']}" if low else "no path"


__all__ = ["FUTURES", "SimulationResult", "amounts_say", "seed_for", "simulate"]
