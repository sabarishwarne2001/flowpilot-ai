"""Library mandate 5 — "Pay every supplier on time".

Every payable is scheduled by its due date on a payment-run day (the book's run weekdays, skipping
the workspace's holidays). As a goal (the default) a late payment is a penalty the optimiser weighs
against the cash floor; set "hard", the due date (plus any grace) becomes a deadline the optimiser
must meet, relaxed only when it cannot be met without breaking the cash floor (and then the
conflict report says so).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.helm import money
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec
from app.services.helm.snapshot import Snapshot

KEY = "pay_on_time"
#: The penalty a late payment costs the plan: once (half a per cent of the invoice) plus 24% a year
#: for each day late, so a day late never beats a few days early at a 12% cost of capital.
LATE_ONCE_PCT = Decimal("0.5")
LATE_RATE_BPS = 2400


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hard: bool = False
    grace_days: int = Field(default=0, ge=0, le=30)


def _readback(spec: MandateSpec, params: Params) -> base.Readback:
    grace = base.Chip("grace_days", params.grace_days,
                      "by its due date" if not params.grace_days else f"within {params.grace_days} days of its due date",
                      "days")
    hard = base.Chip("hard", params.hard, "as a hard deadline" if params.hard else "as a goal", "bool")
    return base.readback("Pay every supplier ", grace, " on a payment-run day, ", hard, ".")


def _evaluate(snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> base.Drift:
    params: Params = mandate.params
    payables = [p for p in base.payables_in_scope(snapshot, mandate) if p.open and p.due_date]
    grace = timedelta(days=params.grace_days)
    overdue = [p for p in payables if p.due_date + grace < snapshot.as_of
               and p.settlement_state not in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED)]
    schedule = context.get("schedule") or {}
    late_in_plan = [p for p in payables if p.promise_id in schedule and p.promise_id not in {o.promise_id for o in overdue}
                    and (schedule[p.promise_id] is None and p.due_date + grace <= snapshot.horizon_end
                         or (schedule[p.promise_id] is not None and schedule[p.promise_id] > p.due_date + grace))]
    items = [{"promise_id": p.promise_id, "document": p.document, "counterparty": p.counterparty_name,
              "currency": p.currency, "amount_micros": p.amount_micros, "due_date": p.due_date.isoformat()}
             for p in overdue + late_in_plan]
    if overdue:
        total = sum(p.amount_micros for p in overdue)
        return base.Drift(base.BREACHED, f"{len(overdue)} overdue", f"{money.say(total, overdue[0].currency)} is past "
                          "its due date and unpaid.", len(overdue), items)
    if late_in_plan:
        return base.Drift(base.DRIFTING, f"{len(late_in_plan)} would pay late",
                          "The plan pays some invoices after their due date to respect other mandates.",
                          len(late_in_plan), items)
    return base.Drift(base.HOLDING, "All on time", "Every open invoice is planned on or before its due date.",
                      0, [])


def _optimizer(snapshot: Snapshot, mandate: base.ActiveMandate) -> base.OptimizerTerms:
    params: Params = mandate.params
    terms = base.OptimizerTerms()
    for payable in base.payables_in_scope(snapshot, mandate):
        if not payable.schedulable or payable.due_date is None:
            continue
        per_day = money.interest(payable.amount_micros, LATE_RATE_BPS, 1)
        once = money.pct_of(payable.amount_micros, LATE_ONCE_PCT)
        terms.late_penalties[payable.promise_id] = (per_day, once)
        if params.hard:
            due = payable.due_date + timedelta(days=params.grace_days)
            terms.deadlines[payable.promise_id] = (due, "ON_TIME", f"due {due.isoformat()} (your pay-on-time rule)")
    return terms


TEMPLATE = base.Template(
    key=KEY, kind=v.MANDATE_KIND_GOAL, title="Pay every supplier on time", group=base.GROUP_RUN, order=5,
    summary="Schedule each payable by its due date on a payment-run day.", Params=Params, readback=_readback,
    evaluate=_evaluate, optimizer=_optimizer,
    keywords=("on time", "due date", "pay suppliers", "late payment", "never late", "pay every supplier"),
)
