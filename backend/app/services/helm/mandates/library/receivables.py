"""Library mandate 11 — "Chase overdue customers on day 3, 10 and 20".

An unpaid sales invoice gets a reminder on each configured day after its due date, at an escalating
tone (friendly, firm, final), from the organization's own sender settings. In Propose mode a person
approves each reminder before it leaves; in Act mode it leaves on its own (reminders are not money,
so the act cap does not apply). Chasing stops the moment a payment is evidenced (a bank statement
line or a person marking it paid). A customer with no known email contact cannot be reminded: the
Guard says so, and Helm settings is where a person adds one.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.helm import money
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec
from app.services.helm.snapshot import Receivable, Snapshot

KEY = "chase_overdue_customers"
TONES = {1: "friendly", 2: "firm", 3: "final"}


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[int] = Field(default_factory=lambda: [3, 10, 20], min_length=1, max_length=3)

    @field_validator("days")
    @classmethod
    def _increasing(cls, value: list[int]) -> list[int]:
        if any(d < 1 or d > 365 for d in value):
            raise ValueError("Reminder days are between 1 and 365 days after the due date.")
        if any(b <= a for a, b in zip(value, value[1:])):
            raise ValueError("Each reminder comes later than the one before it.")
        return value


def level_due(receivable: Receivable, params: Params, as_of) -> Optional[int]:
    """The reminder level due today (1 = the first), or None when not overdue enough."""
    if not receivable.open or receivable.due_date is None:
        return None
    overdue = (as_of - receivable.due_date).days
    reached = [i + 1 for i, day in enumerate(params.days) if overdue >= day]
    return max(reached) if reached else None


def _in_scope(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[Receivable]:
    return [r for r in snapshot.receivables
            if base.in_scope(mandate, workspace_id=r.workspace_id, counterparty_key=r.counterparty_key,
                             currency=r.currency)]


def _readback(spec: MandateSpec, params: Params) -> base.Readback:
    days = base.Chip("days", list(params.days), ", ".join(str(d) for d in params.days[:-1]) +
                     (" and " if len(params.days) > 1 else "") + str(params.days[-1]), "list")
    return base.readback("Remind every customer whose invoice is overdue on day ", days,
                         " after it fell due, more firmly each time, and stop when the payment arrives.")


def _evaluate(snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> base.Drift:
    params: Params = mandate.params
    overdue = [r for r in _in_scope(snapshot, mandate) if r.open and r.due_date and r.due_date < snapshot.as_of]
    if not overdue:
        return base.Drift(base.HOLDING, "Nothing overdue", "Every customer invoice is paid or not yet due.", 0, [])
    behind = [r for r in overdue if (level_due(r, params, snapshot.as_of) or 0) >
              max(snapshot.reminders_sent.get(r.promise_id, (0,)))]
    past_final = [r for r in overdue if (snapshot.as_of - r.due_date).days > params.days[-1] + 14]
    currency = overdue[0].currency
    total = sum(r.amount_micros for r in overdue if r.currency == currency)
    items = [{"promise_id": r.promise_id, "document": r.document, "counterparty": r.counterparty_name,
              "currency": r.currency, "amount_micros": r.amount_micros, "due_date": r.due_date.isoformat(),
              "days_overdue": (snapshot.as_of - r.due_date).days,
              "reminders_sent": list(snapshot.reminders_sent.get(r.promise_id, ()))} for r in overdue]
    if past_final:
        return base.Drift(base.BREACHED, f"{money.say(total, currency)} overdue",
                          f"{len(past_final)} invoice(s) are still unpaid two weeks after the final reminder; a "
                          "person should call the customer.", float(total) / money.MICROS, items)
    return base.Drift(base.DRIFTING, f"{money.say(total, currency)} overdue",
                      f"{len(overdue)} invoice(s) overdue" + (f", {len(behind)} waiting for a reminder." if behind
                                                               else ", every reminder sent on time."),
                      float(total) / money.MICROS, items)


def _propose(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[base.Candidate]:
    params: Params = mandate.params
    out: list[base.Candidate] = []
    for receivable in _in_scope(snapshot, mandate):
        level = level_due(receivable, params, snapshot.as_of)
        if level is None or level <= max(snapshot.reminders_sent.get(receivable.promise_id, (0,))):
            continue
        overdue = (snapshot.as_of - receivable.due_date).days
        customer = receivable.counterparty_name or "the customer"
        out.append(base.Candidate(
            kind=v.ACTION_SEND_REMINDER, mandate=mandate,
            title=f"Remind {customer}: {receivable.document_number or receivable.document} is {overdue} days overdue",
            explanation=(f"{receivable.document} for {money.say(receivable.amount_micros, receivable.currency)} fell "
                         f"due on {money.day(receivable.due_date)} and no payment has been seen. Reminder {level} "
                         f"of {len(params.days)}, in a {TONES.get(level, 'firm')} tone."),
            key_parts=(receivable.promise_id, "reminder", level), workspace_id=receivable.workspace_id,
            work_item_id=receivable.work_item_id, promise_id=receivable.promise_id,
            counterparty_key=receivable.counterparty_key, amount_micros=receivable.amount_micros,
            currency=receivable.currency,
            params={"level": level, "document_number": receivable.document_number,
                    "due_date": receivable.due_date.isoformat(), "days_overdue": overdue}))
    return out


def _forecast(snapshot: Snapshot, mandate: base.ActiveMandate, sim) -> list[base.Risk]:
    """Customers who usually pay late, with an invoice falling due in the next 30 days."""
    out: list[base.Risk] = []
    for receivable in _in_scope(snapshot, mandate):
        if not receivable.open or receivable.due_date is None:
            continue
        days_left = (receivable.due_date - snapshot.as_of).days
        if not 0 <= days_left <= 30:
            continue
        counterparty = snapshot.counterparties.get(receivable.counterparty_key or "")
        history = sorted(int(d) for d in ((counterparty.behaviour or {}).get("days_late") or [])) \
            if counterparty else []
        if len(history) < 3:
            continue  # too few payments to say how this customer usually pays
        typical = history[len(history) // 2]
        if typical < 7:
            continue
        out.append(base.Risk(
            mandate=KEY, on=receivable.due_date, currency=receivable.currency,
            amount_micros=receivable.amount_micros,
            summary=(f"{receivable.counterparty_name or 'A customer'} usually pays about {int(typical)} days late; "
                     f"{receivable.document} falls due on {money.day(receivable.due_date)}.")))
    return out



def _backtest(history, mandate: base.ActiveMandate) -> list[base.Finding]:
    params: Params = mandate.params
    out: list[base.Finding] = []
    for receivable in _in_scope(history.snapshot, mandate):
        if receivable.due_date is None or not history.within(receivable.due_date):
            continue
        first = receivable.due_date + timedelta(days=params.days[0])
        if receivable.paid_on is not None and receivable.paid_on > first:
            honesty = v.HONESTY_CONFIRMED
            what = f"was paid {(receivable.paid_on - receivable.due_date).days} days late"
        elif receivable.paid_on is None and receivable.open and first < history.end:
            honesty, what = v.HONESTY_LIKELY, f"is {(history.end - receivable.due_date).days} days overdue"
        else:
            continue
        out.append(base.Finding(
            mandate=KEY, on=first, action=v.ACTION_SEND_REMINDER, honesty=honesty,
            category=base.CATEGORY_RECEIVABLE, currency=receivable.currency, value_micros=0,
            summary=(f"{receivable.document} from {receivable.counterparty_name or 'a customer'} "
                     f"({money.say(receivable.amount_micros, receivable.currency)}) {what}. Helm would have "
                     f"reminded on day {', '.join(str(d) for d in params.days)}."),
            evidence=[{"work_item_id": receivable.work_item_id, "document": receivable.document}]))
    return out


TEMPLATE = base.Template(
    key=KEY, kind=v.MANDATE_KIND_GOAL, title="Chase overdue customers", group=base.GROUP_RECEIVABLES, order=11,
    summary="Reminders on day 3, 10 and 20 after the due date, more firmly each time, until the payment arrives.",
    Params=Params, readback=_readback, evaluate=_evaluate, propose=_propose, forecast=_forecast, backtest=_backtest,
    keywords=("overdue", "remind", "reminder", "chase", "collect", "receivable", "customers pay", "dunning",
              "late customers"),
    recommended_for=("distributor", "manufacturer", "logistics", "accounting_firm"),
)
