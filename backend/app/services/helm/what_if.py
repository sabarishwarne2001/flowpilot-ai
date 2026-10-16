"""What-if: the book's plan and cash flight path under a change, beside today's, saving nothing.

    POST /organizations/{oid}/helm/books/{bid}/what-if
         {cash_change_micros?, currency?, delay_receivables_days?, hold_promise_ids?, horizon_days?}

HOW IT WORKS
============

1. The book's snapshot is taken the way a plan takes it (`snapshot_store.take`).
2. The scenario is applied to a COPY (`dataclasses.replace`; a `Snapshot` is frozen data):
   * a **cash change** moves today's balance in one currency up or down, once;
   * a **receivables delay** makes every open customer invoice arrive that many days later than the
     customer's own history says (`Receivable.expected_delay_days`). The due dates do not move, so
     "chase overdue customers" still sees each invoice as overdue as it really is;
   * a **hold** takes the named invoices we owe out of the plan, as if a person had held them.
3. The Planner's composition (`planner.compose`: drift, candidates, payment timing, the simulation,
   the Guard) runs on today's snapshot and on the copy. The copy keeps today's digest, so both runs
   use the same seed and draw the same 1,000 futures: any difference is the scenario's, not chance.
4. The answer compares them: both flight paths (`horizon_days`, 7 to 90, default the cockpit's 90),
   both probabilities of staying above the cash floor, the mandates whose state changes, the plan's
   payments, and one paragraph in plain words.

NOTHING IS SAVED
================

No plan row, action, certificate, audit row or notice; counterparties are not re-synced (a plan does
that). The route ends its transaction with a rollback, which also releases the ledger's shared locks
the snapshot took. Same gate as "Re-plan": `capability.helm`, or the sample company.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional, Sequence

from app.services.helm import money, planner, simulation
from app.services.helm import vocabulary as v
from app.services.helm.snapshot import Payable, Snapshot

MAX_HOLDS = 50
MAX_DELAY_DAYS = 90
MIN_HORIZON_DAYS = 7
MAX_HORIZON_DAYS = 90
DEFAULT_HORIZON_DAYS = 90
MAX_CASH_CHANGE_MICROS = 1_000_000_000_000 * money.MICROS
#: Each of the two compositions gets this long in the solver: a what-if is a question someone waits on.
TIME_LIMIT_S = 3.0
HOLD_REASON = "Held in a what-if."
MASK = "hidden"
#: How many mandate changes the summary names before it counts the rest.
NAMED_CHANGES = 3

_CURRENCY = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class Scenario:
    cash_change_micros: int = 0
    currency: Optional[str] = None
    delay_receivables_days: int = 0
    hold_promise_ids: tuple[str, ...] = ()
    horizon_days: int = DEFAULT_HORIZON_DAYS

    @property
    def changes_anything(self) -> bool:
        return bool(self.cash_change_micros or self.delay_receivables_days or self.hold_promise_ids)

    def as_json(self) -> dict[str, Any]:
        return {"cash_change_micros": self.cash_change_micros, "currency": self.currency,
                "delay_receivables_days": self.delay_receivables_days,
                "hold_promise_ids": list(self.hold_promise_ids), "horizon_days": self.horizon_days}


def _int(raw: Mapping[str, Any], key: str) -> Optional[int]:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(key)
    return value


def parse(raw: Mapping[str, Any]) -> tuple[Scenario, list[str]]:
    """The request as a `Scenario`, and what is wrong with it in plain words (an empty list: nothing).
    Nothing here needs the book; `check` does the rest once the snapshot is taken."""
    problems: list[str] = []
    try:
        change = _int(raw, "cash_change_micros") or 0
    except TypeError:
        change = 0
        problems.append("The cash change is a whole number of millionths of the currency.")
    if abs(change) > MAX_CASH_CHANGE_MICROS:
        problems.append("A cash change can be at most 1,000,000,000,000 up or down.")
        change = 0
    currency = raw.get("currency")
    if currency is not None:
        currency = str(currency).strip().upper() or None
        if currency is not None and not _CURRENCY.match(currency):
            problems.append("The currency is a three-letter code, such as INR.")
            currency = None
    try:
        delay = _int(raw, "delay_receivables_days") or 0
    except TypeError:
        delay = 0
        problems.append("The delay is a whole number of days.")
    if not 0 <= delay <= MAX_DELAY_DAYS:
        problems.append(f"Customers can be delayed by 0 to {MAX_DELAY_DAYS} days.")
        delay = 0
    try:
        horizon = _int(raw, "horizon_days")
    except TypeError:
        horizon = None
        problems.append("How far ahead to look is a whole number of days.")
    if horizon is None:
        horizon = DEFAULT_HORIZON_DAYS
    elif not MIN_HORIZON_DAYS <= horizon <= MAX_HORIZON_DAYS:
        problems.append(f"Helm looks {MIN_HORIZON_DAYS} to {MAX_HORIZON_DAYS} days ahead in a what-if.")
        horizon = DEFAULT_HORIZON_DAYS
    holds: list[str] = []
    listed = raw.get("hold_promise_ids") or []
    if not isinstance(listed, (list, tuple)):
        problems.append("The invoices to hold are a list.")
        listed = []
    if len(listed) > MAX_HOLDS:
        problems.append(f"Hold at most {MAX_HOLDS} invoices in one what-if.")
        listed = []
    for position, value in enumerate(listed, start=1):
        try:
            found = str(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            problems.append(f"Invoice {position} of the list to hold is not an invoice you owe in this book.")
            continue
        if found not in holds:
            holds.append(found)
    scenario = Scenario(cash_change_micros=change, currency=currency, delay_receivables_days=delay,
                        hold_promise_ids=tuple(holds), horizon_days=horizon)
    if not problems and not scenario.changes_anything:
        problems.append("Tell Helm what to change: the cash you have today, how late customers pay, or invoices "
                        "to hold.")
    return scenario, problems


def check(scenario: Scenario, snapshot: Snapshot) -> tuple[Scenario, list[str]]:
    """Check the scenario against the book; fill in the cash change's currency when the book holds
    cash in one currency only. Returns the scenario to run and the problems in plain words."""
    problems: list[str] = []
    currency = scenario.currency
    if scenario.cash_change_micros:
        held = sorted(snapshot.cash)
        if not held:
            problems.append("Helm does not know this book's cash yet. Type a cash position in Helm settings, or "
                            "import a bank statement, then ask again.")
        elif currency is None:
            if len(held) == 1:
                currency = held[0]
            else:
                problems.append(f"This book holds cash in {_and(held)}; say which currency the change is in.")
        elif currency not in snapshot.cash:
            problems.append(f"This book has no {currency} cash position; it holds cash in {_and(held)}.")
    for position, promise_id in enumerate(scenario.hold_promise_ids, start=1):
        payable = snapshot.payable(promise_id)
        if payable is None:
            problems.append(f"Invoice {position} of the list to hold is not an invoice you owe in this book.")
        elif not payable.open:
            problems.append(f"{_label(payable, position)} is already paid, so holding it changes nothing.")
    return replace(scenario, currency=currency if scenario.cash_change_micros else None), problems


def _held(payable: Payable) -> Payable:
    if payable.settlement_state in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED):
        return payable  # already out of the plan
    return replace(payable, settlement_state=v.SETTLEMENT_HELD, settlement_source=v.SOURCE_PERSON,
                   hold_reason=HOLD_REASON, hold_code=None, run_date=None)


def apply(snapshot: Snapshot, scenario: Scenario) -> Snapshot:
    """The scenario applied to a copy of the snapshot. The original is untouched (it is frozen), and
    the copy keeps its digest so the simulation draws the same futures for both."""
    cash = dict(snapshot.cash)
    if scenario.cash_change_micros and scenario.currency in cash:
        position = cash[scenario.currency]
        cash[scenario.currency] = replace(position, amount_micros=position.amount_micros + scenario.cash_change_micros)
    receivables = snapshot.receivables
    if scenario.delay_receivables_days:
        receivables = tuple(
            replace(r, expected_delay_days=r.expected_delay_days + scenario.delay_receivables_days) if r.open else r
            for r in snapshot.receivables)
    holds = set(scenario.hold_promise_ids)
    payables = tuple(_held(p) if p.promise_id in holds and p.open else p for p in snapshot.payables)
    return replace(snapshot, cash=cash, receivables=receivables, payables=payables)


# ---------------------------------------------------------------------------
# Comparing the two compositions
# ---------------------------------------------------------------------------


def _and(items: Sequence[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _label(payable: Payable, position: int) -> str:
    return f"Invoice {payable.document_number}" if payable.document_number else f"Invoice {position} of the list"


def _lowest(path: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not path:
        return None
    low = min(path, key=lambda p: (int(p["p10"]), p["on"]))
    return {"on": low["on"], "p10": int(low["p10"])}


def _side(composition: planner.Composition, *, readable: bool) -> dict[str, Any]:
    forecast = composition.forecast_sim
    paths = forecast.cash_paths if forecast else {}
    summary = composition.summary()
    conflict = composition.conflict
    if conflict is not None:
        conflict = {"headline": conflict.get("headline"), "options": list(conflict.get("options") or [])
                    if readable else []}
    return {
        "cash_paths": paths,
        "floor_probability": dict(forecast.floor_probability) if forecast else {},
        "lowest": {c: _lowest(path) for c, path in paths.items() if path},
        "end": {c: path[-1] for c, path in paths.items() if path},
        "cash_today": {c: x.amount_micros for c, x in composition.snapshot.cash.items()},
        "status": composition.status,
        "conflict": conflict,
        "payments": summary["counts"]["payments"],
        "payments_by_currency": summary["payments_by_currency"],
        "deferred": len(summary["optimizer"]["deferred"]),
    }


def _changes(baseline: planner.Composition, scenario: planner.Composition, *, readable: bool) -> list[dict[str, Any]]:
    out = []
    for mandate in baseline.mandates:
        before, after = baseline.drifts.get(mandate.id), scenario.drifts.get(mandate.id)
        if before is None or after is None or before.state == after.state:
            continue
        out.append({
            "mandate_id": mandate.id, "title": mandate.title, "template": mandate.template,
            "baseline": {"state": before.state, "figure": before.figure,
                         "detail": before.detail if readable else MASK},
            "scenario": {"state": after.state, "figure": after.figure, "detail": after.detail if readable else MASK},
        })
    return out


def _clauses(scenario: Scenario, snapshot: Snapshot) -> list[str]:
    out = []
    change = scenario.cash_change_micros
    if change:
        out.append(f"cash were {money.say(abs(change), scenario.currency)} {'lower' if change < 0 else 'higher'} "
                   "today")
    if scenario.delay_receivables_days:
        out.append(f"customers paid {_plural(scenario.delay_receivables_days, 'day')} later than usual")
    if scenario.hold_promise_ids:
        labels = [(snapshot.payable(pid).document_number or "an invoice") for pid in scenario.hold_promise_ids
                  if snapshot.payable(pid) is not None]
        n = len(scenario.hold_promise_ids)
        named = f" ({_and(labels[:5])}{' and others' if len(labels) > 5 else ''})" if labels else ""
        out.append(f"{_plural(n, 'invoice')} {'was' if n == 1 else 'were'} held{named}")
    return out


def _out_of_100(p: float) -> int:
    return int(round(p * 100))


def _cash_sentences(base: dict[str, Any], what: dict[str, Any], floors: dict[str, int], horizon: int) -> list[str]:
    out = []
    currencies = sorted(set(what["cash_paths"]) | set(base["cash_paths"]))
    for currency in currencies:
        floor = floors.get(currency)
        p_base, p_what = base["floor_probability"].get(currency), what["floor_probability"].get(currency)
        low_base, low_what = base["lowest"].get(currency), what["lowest"].get(currency)
        if floor is not None and p_base is not None and p_what is not None:
            b, w = _out_of_100(p_base), _out_of_100(p_what)
            compared = (f"down from {b}" if w < b else f"up from {b}" if w > b else "the same") + " as things stand"
            out.append(f"{currency} cash would stay above the {money.say(floor, currency)} floor in {w} of 100 "
                       f"simulated futures over the next {horizon} days, {compared}.")
            lead = "Its lowest cautious point"
        else:
            lead = f"{currency} cash's lowest cautious point over the next {horizon} days"
        if low_what is None or low_base is None:
            continue
        if (low_what["p10"], low_what["on"]) == (low_base["p10"], low_base["on"]):
            out.append(f"{lead} stays {money.say(low_what['p10'], currency)} on {money.day(low_what['on'])}.")
        else:
            out.append(f"{lead} would be {money.say(low_what['p10'], currency)} on {money.day(low_what['on'])}, "
                       f"against {money.say(low_base['p10'], currency)} on {money.day(low_base['on'])} as things "
                       "stand.")
    return out


def describe(scenario: Scenario, snapshot: Snapshot, base: dict[str, Any], what: dict[str, Any],
             changes: list[dict[str, Any]], floors: dict[str, int]) -> str:
    """The what-if in one paragraph of plain words. Deterministic: no model writes it."""
    clauses = _clauses(scenario, snapshot)
    sentences = []
    cash = _cash_sentences(base, what, floors, scenario.horizon_days)
    opening = f"If {_and(clauses)}" if clauses else "With this change"
    if cash:
        sentences.append(f"{opening}, {cash[0]}")
        sentences.extend(cash[1:])
    else:
        sentences.append(f"{opening}, Helm could not draw a flight path: it does not know this book's cash. Type a "
                         "cash position in Helm settings to see one.")
    if scenario.delay_receivables_days and not any(r.open for r in snapshot.receivables):
        sentences.append("No customer invoice is open, so paying later changes nothing yet.")
    if changes:
        named = [f"{c['title']} would go from {c['baseline']['state']} to {c['scenario']['state']}."
                 for c in changes[:NAMED_CHANGES]]
        sentences.extend(named)
        if len(changes) > NAMED_CHANGES:
            rest = len(changes) - NAMED_CHANGES
            sentences.append(f"{_plural(rest, 'more mandate')} {'changes' if rest == 1 else 'change'} too.")
    else:
        sentences.append("No mandate changes state.")
    if what["payments"] != base["payments"]:
        sentences.append(f"The plan would schedule {_plural(what['payments'], 'payment')} instead of "
                         f"{base['payments']}.")
    if what["conflict"] and not base["conflict"]:
        sentences.append(f"The plan would then have a conflict: {what['conflict']['headline']}")
    elif base["conflict"] and not what["conflict"]:
        sentences.append("The plan's conflict would go away.")
    if v.SOLVER_APPROXIMATE in (base["status"], what["status"]):
        sentences.append("The solver ran out of time on one of the two plans, so the comparison is approximate.")
    return " ".join(sentences)


def compare(baseline: planner.Composition, scenario_run: planner.Composition, scenario: Scenario, *,
            readable: bool = True) -> dict[str, Any]:
    """Baseline against scenario, as the API returns it."""
    base = _side(baseline, readable=readable)
    what = _side(scenario_run, readable=readable)
    changes = _changes(baseline, scenario_run, readable=readable)
    floors = planner.cash_floors(baseline.mandates)
    snapshot = baseline.snapshot
    return {
        "as_of": snapshot.as_of.isoformat(), "horizon_days": scenario.horizon_days, "futures": simulation.FUTURES,
        "seed": str(baseline.seed), "inputs": scenario.as_json(), "floors": floors,
        "baseline": base, "scenario": what, "mandates": changes,
        "summary": describe(scenario, snapshot, base, what, changes, floors),
        "approximate": v.SOLVER_APPROXIMATE in (base["status"], what["status"]),
        "readable": readable, "saved": False,
        "note": "Nothing was saved: no plan, no action and no audit entry. Helm's real plan is unchanged.",
    }


def simulate(snapshot: Snapshot, mandates: Sequence[Any], scenario: Scenario, *, capability_held: bool = True,
             readable: bool = True, time_limit_s: float = TIME_LIMIT_S) -> dict[str, Any]:
    """Compose today's plan and the scenario's from one snapshot and compare them (pure)."""
    changed = apply(snapshot, scenario)
    kwargs = {"capability_held": capability_held, "time_limit_s": time_limit_s,
              "forecast_days": scenario.horizon_days}
    baseline = planner.compose(snapshot, mandates, **kwargs)
    scenario_run = planner.compose(changed, mandates, **kwargs)
    return compare(baseline, scenario_run, scenario, readable=readable)


# ---------------------------------------------------------------------------
# The database side (reads only)
# ---------------------------------------------------------------------------


def run(db, who, book, raw: Mapping[str, Any]) -> dict[str, Any]:
    """The what-if for one book, as the person asking may see it. Reads only; the caller rolls back."""
    from app.services.helm import plan_store, service, snapshot_store
    from app.services.helm.errors import HelmForbidden, HelmInvalid

    service.require_helm(who, book)
    scenario, problems = parse(raw)
    if problems:
        raise HelmInvalid(" ".join(problems), reason="WHAT_IF_INVALID", details={"problems": problems})
    snapshot = snapshot_store.take(db, book)
    for promise_id in scenario.hold_promise_ids:
        payable = snapshot.payable(promise_id)
        if payable is not None and not who.can_read(uuid.UUID(payable.workspace_id)):
            raise HelmForbidden("One of the invoices to hold is in a workspace you cannot read.",
                                reason="HELM_NO_ACCESS")
    scenario, problems = check(scenario, snapshot)
    if problems:
        raise HelmInvalid(" ".join(problems), reason="WHAT_IF_INVALID", details={"problems": problems})
    mandates = plan_store.active_mandates(db, book.id)
    readable = who.can_read(book.workspace_id) if book.kind != v.BOOK_ORGANIZATION else True
    return simulate(snapshot, mandates, scenario, capability_held=who.helm or book.kind == v.BOOK_SAMPLE,
                    readable=readable)


__all__ = ["DEFAULT_HORIZON_DAYS", "HOLD_REASON", "MAX_DELAY_DAYS", "MAX_HOLDS", "Scenario", "apply", "check",
           "compare", "describe", "parse", "run", "simulate"]
