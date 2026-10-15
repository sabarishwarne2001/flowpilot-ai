"""Library mandate 7 — "Keep cash above a floor" (one currency).

A set point the optimiser respects: on every payment-run day, opening cash plus the simulation's
conservative (10th-percentile) inflows minus the plan's payments stays at or above the floor. The
simulation reports the probability that it holds over the horizon; below the mandate's minimum
probability the instrument shows drifting even when the plan itself meets the floor.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.helm import money
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec, Money
from app.services.helm.snapshot import Snapshot

KEY = "keep_cash_above"


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    floor: Money
    min_probability_pct: int = Field(default=90, ge=50, le=99)


def _readback(spec: MandateSpec, params: Params) -> base.Readback:
    floor = base.Chip("floor", params.floor.model_dump(), money.say(params.floor.amount_micros, params.floor.currency),
                      "money")
    probability = base.Chip("min_probability_pct", params.min_probability_pct, f"{params.min_probability_pct}%",
                            "percent")
    return base.readback("Keep ", base.Chip("currency", params.floor.currency, params.floor.currency, "text"),
                         " cash above ", floor, " on every payment-run day, with at least ", probability,
                         " confidence.")


def _validate(spec: MandateSpec, params: Params) -> list[str]:
    problems = []
    if spec.scope.currency and spec.scope.currency != params.floor.currency:
        problems.append(f"The floor is in {params.floor.currency} but the mandate covers {spec.scope.currency} only.")
    return problems


def _probability(context: dict, currency: str) -> Optional[float]:
    sim = context.get("sim")
    if sim is None:
        return None
    return (sim.get("floor_probability") or {}).get(currency) if isinstance(sim, dict) else sim.floor_probability.get(
        currency)


def _min_cash(context: dict, currency: str) -> Optional[int]:
    path = (context.get("cash_paths") or {}).get(currency)
    if not path:
        return None
    return min(int(point["p10"]) for point in path)


def _evaluate(snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> base.Drift:
    params: Params = mandate.params
    currency, floor = params.floor.currency, params.floor.amount_micros
    cash = snapshot.cash.get(currency)
    if cash is None:
        return base.Drift(base.UNKNOWN, "No cash position", f"Helm does not know the {currency} cash position; add a "
                          "statement or type the balance in Helm settings.", None, [])
    if cash.amount_micros < floor:
        return base.Drift(base.BREACHED, money.say(cash.amount_micros, currency),
                          f"Cash is {money.say(floor - cash.amount_micros, currency)} below the floor now.",
                          float(cash.amount_micros) / money.MICROS, [])
    lowest = _min_cash(context, currency)
    probability = _probability(context, currency)
    if lowest is not None and lowest < floor:
        return base.Drift(base.DRIFTING, f"Low point {money.say(lowest, currency)}",
                          "On the cautious path, cash dips below the floor within the horizon.",
                          float(lowest) / money.MICROS, [])
    if probability is not None and probability * 100 < params.min_probability_pct:
        return base.Drift(base.DRIFTING, f"{round(probability * 100)}% likely to hold",
                          f"Below the {params.min_probability_pct}% this mandate asks for.", probability, [])
    shown = probability if probability is not None else None
    return base.Drift(base.HOLDING, money.say(cash.amount_micros, currency),
                      f"Above the floor of {money.say(floor, currency)}"
                      + (f"; {round(shown * 100)}% likely to hold over the horizon." if shown is not None else "."),
                      float(cash.amount_micros) / money.MICROS, [])


def _guard(action: Any, snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> list[base.Check]:
    if action.kind not in (v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT):
        return []
    params: Params = mandate.params
    if action.currency != params.floor.currency:
        return []
    cash = snapshot.cash.get(action.currency)
    if cash is None:
        return [base.Check(f"limit:{KEY}", "Cash stays above the floor", v.CHECK_APPROVAL,
                           f"No {action.currency} cash position is known, so a person decides.", {})]
    ahead = int((context.get("scheduled_before") or {}).get(action.currency, 0))
    inflow = int((context.get("inflow_by") or {}).get(action.currency, 0))
    after = cash.amount_micros + inflow - ahead - int(action.amount_micros or 0)
    ok = after >= params.floor.amount_micros
    return [base.check(
        f"limit:{KEY}", "Cash stays above the floor", ok,
        detail=(f"On the cautious path, {action.currency} cash after this payment is {money.say(after, action.currency)}"
                f" against a floor of {money.say(params.floor.amount_micros, action.currency)}."),
        evidence={"cash_micros": cash.amount_micros, "cash_as_of": cash.as_of.isoformat(), "inflow_p10_micros": inflow,
                  "paid_before_micros": ahead, "after_micros": after, "floor_micros": params.floor.amount_micros})]


def _optimizer(snapshot: Snapshot, mandate: base.ActiveMandate) -> base.OptimizerTerms:
    params: Params = mandate.params
    return base.OptimizerTerms(floors={params.floor.currency: params.floor.amount_micros})



def _backtest(history, mandate: base.ActiveMandate) -> list[base.Finding]:
    params: Params = mandate.params
    floor = params.floor
    out: list[base.Finding] = []
    for cash in history.cash:
        if cash.currency != floor.currency or not history.within(cash.as_of) or cash.amount_micros >= floor.amount_micros:
            continue
        statement = cash.source in (v.CASH_SOURCE_STATEMENT, v.CASH_SOURCE_BANK)
        missing = len(cash.evidence.get("accounts_without_balance") or []) if cash.evidence else 0
        honesty = (v.HONESTY_POTENTIAL if missing else v.HONESTY_CONFIRMED) if statement else v.HONESTY_LIKELY
        source = "a bank statement" if statement else "a typed position"
        if missing:
            source += f"; {missing} bank account{'s' if missing != 1 else ''} had no balance yet, so the total may be low"
        out.append(base.Finding(
            mandate=KEY, on=cash.as_of, action="observe", honesty=honesty,
            category=base.CATEGORY_CASH_FLOOR, currency=cash.currency, value_micros=0,
            summary=(f"Cash was {money.say(cash.amount_micros, cash.currency)} on {money.day(cash.as_of)}, "
                     f"{money.say(floor.amount_micros - cash.amount_micros, cash.currency)} below the floor "
                     f"({source}). Helm would have timed payments to keep it above."),
            evidence=[cash.evidence] if cash.evidence else []))
    return out


TEMPLATE = base.Template(
    key=KEY, kind=v.MANDATE_KIND_SET_POINT, title="Keep cash above a floor", group=base.GROUP_RUN, order=7,
    summary="A floor the optimiser respects in one currency; the simulation says how likely it holds.",
    Params=Params, readback=_readback, evaluate=_evaluate, guard_checks=_guard, optimizer=_optimizer, backtest=_backtest,
    validate=_validate, is_limit=True,
    keywords=("cash above", "cash floor", "minimum cash", "keep cash", "balance above", "never below", "lakh", "crore",
              "cash buffer"),
)
