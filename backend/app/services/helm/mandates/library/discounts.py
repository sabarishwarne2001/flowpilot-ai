"""Library mandate 6 — "Capture early-payment discounts worth more than X% a year".

A goal for the optimiser: an invoice whose terms offer a discount for paying early ("2/10 net 30")
is worth paying inside the window when the discount, as a yearly rate, beats the threshold. The
optimiser weighs each one against the cash floor and every other mandate; it never takes a discount
by breaking one. Terms Helm could not read are never assumed to carry a discount.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.helm import money
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec
from app.services.helm.snapshot import Payable, Snapshot

KEY = "capture_early_discounts"


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_annual_rate_pct: Decimal = Field(default=Decimal("18"), ge=0, le=1000)


def worth_taking(payable: Payable, params: Params, as_of) -> Optional[Decimal]:
    """The yearly rate of a discount still on offer and above the threshold, else None."""
    if not payable.schedulable or not payable.terms.has_discount:
        return None
    deadline = payable.discount_deadline
    if deadline is None or deadline < as_of:
        return None
    rate = payable.terms.annualised_discount_rate()
    if rate is None or rate < params.min_annual_rate_pct:
        return None
    return rate


def _readback(spec: MandateSpec, params: Params) -> base.Readback:
    rate = base.Chip("min_annual_rate_pct", str(params.min_annual_rate_pct),
                     f"{params.min_annual_rate_pct.normalize()}% a year", "percent")
    return base.readback("Take every early-payment discount worth more than ", rate,
                         ", while every other mandate still holds.")


def _evaluate(snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> base.Drift:
    params: Params = mandate.params
    schedule = context.get("schedule") or {}
    offered = [(p, worth_taking(p, params, snapshot.as_of)) for p in base.payables_in_scope(snapshot, mandate)]
    offered = [(p, rate) for p, rate in offered if rate is not None]
    if not offered:
        return base.Drift(base.HOLDING, "No discounts on offer",
                          "No open invoice offers an early-payment discount above the rate.", 0, [])
    taken = [(p, r) for p, r in offered if schedule.get(p.promise_id) and p.discount_deadline
             and schedule[p.promise_id] <= p.discount_deadline]
    missed = [(p, r) for p, r in offered if (p, r) not in taken]
    currency = offered[0][0].currency
    items = [{"promise_id": p.promise_id, "document": p.document, "counterparty": p.counterparty_name,
              "currency": p.currency, "discount_micros": p.discount_micros, "annual_rate_pct": str(r),
              "discount_deadline": p.discount_deadline.isoformat() if p.discount_deadline else None,
              "in_plan": (p, r) in taken} for p, r in offered]
    taken_value = sum(p.discount_micros for p, _ in taken if p.currency == currency)
    if missed and not schedule:
        return base.Drift(base.UNKNOWN, f"{len(offered)} discount(s) on offer",
                          "Add a pay-on-time or cash-floor mandate so Helm can plan payment timing.", len(offered),
                          items)
    if missed:
        lost = sum(p.discount_micros for p, _ in missed if p.currency == currency)
        return base.Drift(base.DRIFTING, f"{money.say(lost, currency)} in discounts the plan cannot take",
                          f"{len(missed)} discount(s) cannot be taken without breaking another mandate (usually the "
                          "cash floor).", float(lost) / money.MICROS, items)
    return base.Drift(base.HOLDING, f"{money.say(taken_value, currency)} in discounts planned",
                      f"Every discount above {params.min_annual_rate_pct.normalize()}% a year is in the plan.",
                      float(taken_value) / money.MICROS, items)


def _optimizer(snapshot: Snapshot, mandate: base.ActiveMandate) -> base.OptimizerTerms:
    terms = base.OptimizerTerms()
    for payable in base.payables_in_scope(snapshot, mandate):
        if worth_taking(payable, mandate.params, snapshot.as_of) is not None and payable.discount_micros > 0:
            terms.discounts[payable.promise_id] = payable.discount_micros
    return terms


def _forecast(snapshot: Snapshot, mandate: base.ActiveMandate, sim) -> list[base.Risk]:
    """Discounts whose window closes within a week, so a person can find the cash in time."""
    out: list[base.Risk] = []
    for payable in base.payables_in_scope(snapshot, mandate):
        rate = worth_taking(payable, mandate.params, snapshot.as_of)
        deadline = payable.discount_deadline
        if rate is None or deadline is None or (deadline - snapshot.as_of).days > 7:
            continue
        out.append(base.Risk(
            mandate=KEY, on=deadline, currency=payable.currency, amount_micros=payable.discount_micros,
            summary=(f"The {money.say(payable.discount_micros, payable.currency)} discount on {payable.document} "
                     f"({rate.normalize()}% a year) closes on {money.day(deadline)}.")))
    return out



def _backtest(history, mandate: base.ActiveMandate) -> list[base.Finding]:
    params: Params = mandate.params
    out: list[base.Finding] = []
    for payable in base.payables_in_scope(history.snapshot, mandate):
        deadline = payable.discount_deadline
        rate = payable.terms.annualised_discount_rate()
        if deadline is None or rate is None or rate < params.min_annual_rate_pct or not history.within(deadline):
            continue
        if payable.paid_on is not None and payable.paid_on <= deadline:
            continue  # taken already
        if payable.paid_on is not None:
            honesty = v.HONESTY_CONFIRMED
            what = f"was paid on {money.day(payable.paid_on)}, after the discount closed on {money.day(deadline)}"
        elif deadline < history.end:
            honesty, what = v.HONESTY_POTENTIAL, f"had a discount until {money.day(deadline)} and no payment is recorded"
        else:
            continue
        out.append(base.Finding(
            mandate=KEY, on=deadline, action=v.ACTION_SCHEDULE_PAYMENT, honesty=honesty,
            category=v.VALUE_DISCOUNT_CAPTURED, currency=payable.currency, value_micros=payable.discount_micros,
            summary=(f"{payable.document} {what}: {money.say(payable.discount_micros, payable.currency)} missed "
                     f"({rate.normalize()}% a year). Helm would have paid it inside the window."),
            evidence=base.evidence_of(payable)))
    return out


TEMPLATE = base.Template(
    key=KEY, kind=v.MANDATE_KIND_GOAL, title="Capture early-payment discounts", group=base.GROUP_RUN, order=6,
    summary="Pay early when a discount beats your cost of money, while every other mandate holds.", Params=Params,
    readback=_readback, evaluate=_evaluate, optimizer=_optimizer, forecast=_forecast, backtest=_backtest,
    keywords=("discount", "early payment", "pay early", "2/10", "cash discount", "early-payment"),
    recommended_for=("distributor", "manufacturer", "retailer"),
)
