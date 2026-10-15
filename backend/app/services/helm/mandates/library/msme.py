"""Library mandate 8 (India pack) — "Pay micro and small suppliers within the MSMED Act limit".

Section 15 of the MSMED Act, 2006: a buyer pays a micro or small enterprise by the agreed date when
that is 45 days or less from acceptance, else within 15 days of acceptance. Late payment owes the
supplier compound interest at three times the bank rate (section 16), and since the Finance Act 2023
the buyer also loses the income-tax deduction for that expense in the year (section 43B(h)) unless
it is paid within the limit.

Helm's stated reading (shown on every explanation): acceptance is the goods-receipt date, else the
invoice date (conservative); "agreed" means stated terms of 45 days or less, else 15 days. A
supplier with a Udyam number whose category nobody confirmed is treated as micro or small. The
deadline is a hard constraint for the optimiser; if cash cannot cover it, the conflict report says so
in words. Recommendations, not legal or tax advice.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.helm import money
from app.services.helm import msme as rules
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec
from app.services.helm.snapshot import Payable, Snapshot

KEY = "msme_pay_within_limit"
CONSEQUENCE = ("Paying later owes the supplier interest at three times the bank rate (MSMED Act section 16) and "
               "loses the income-tax deduction for this expense this year (section 43B(h)).")


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buffer_days: int = Field(default=0, ge=0, le=14)


def deadline_of(payable: Payable, params: Params):
    if not payable.open or payable.msme_deadline is None:
        return None
    return payable.msme_deadline - timedelta(days=params.buffer_days)


def _readback(spec: MandateSpec, params: Params) -> base.Readback:
    buffer = base.Chip("buffer_days", params.buffer_days,
                       "by the legal deadline" if not params.buffer_days else
                       f"{params.buffer_days} days before the legal deadline", "days")
    return base.readback("Pay every micro and small supplier ", buffer,
                         " (MSMED Act section 15: the agreed date within 45 days of acceptance, else 15 days), "
                         "treating a supplier whose category is unconfirmed as micro or small.")


def _evaluate(snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> base.Drift:
    params: Params = mandate.params
    schedule = context.get("schedule") or {}
    protected = [p for p in base.payables_in_scope(snapshot, mandate) if deadline_of(p, params)]
    if not protected:
        return base.Drift(base.HOLDING, "No MSME invoices open",
                          "No open invoice from a micro or small supplier.", 0, [])
    held = (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED)
    late = [p for p in protected if p.msme_deadline < snapshot.as_of]
    at_risk = [p for p in protected if p not in late and p.settlement_state not in held and (
        (p.promise_id in schedule and (schedule[p.promise_id] is None or schedule[p.promise_id] > p.msme_deadline))
        or (schedule == {} and p.settlement_state == v.SETTLEMENT_UNPAID and p.msme_deadline <= snapshot.horizon_end))]
    items = [{"promise_id": p.promise_id, "document": p.document, "counterparty": p.counterparty_name,
              "currency": p.currency, "amount_micros": p.amount_micros, "msme_deadline": p.msme_deadline.isoformat(),
              "explanation": p.msme_explanation, "udyam_number": p.udyam_number} for p in protected]
    if late:
        total = sum(p.amount_micros for p in late)
        return base.Drift(base.BREACHED, f"{len(late)} past the legal limit",
                          f"{money.say(total, late[0].currency)} to micro or small suppliers is past the MSMED Act "
                          "deadline. " + CONSEQUENCE, len(late), items)
    if at_risk:
        return base.Drift(base.DRIFTING, f"{len(at_risk)} at risk of paying late",
                          "Some MSME invoices are not planned inside the legal limit. " + CONSEQUENCE, len(at_risk),
                          items)
    nearest = min(p.msme_deadline for p in protected)
    return base.Drift(base.HOLDING, f"All {len(protected)} inside the limit",
                      f"Every MSME invoice is planned inside the legal limit; the nearest deadline is "
                      f"{money.day(nearest)}.", len(protected), items)


def _optimizer(snapshot: Snapshot, mandate: base.ActiveMandate) -> base.OptimizerTerms:
    params: Params = mandate.params
    terms = base.OptimizerTerms()
    for payable in base.payables_in_scope(snapshot, mandate):
        due = deadline_of(payable, params)
        if due is None or not payable.schedulable:
            continue
        why = payable.msme_explanation or "the MSMED Act limit"
        terms.deadlines[payable.promise_id] = (max(due, snapshot.as_of), "MSME", f"MSMED Act section 15: {why}")
    return terms


def _guard(action: Any, snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> list[base.Check]:
    """A payment after the legal deadline still goes ahead (later is worse), but a person approves it
    knowing the consequence."""
    if action.kind not in (v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT):
        return []
    payable = snapshot.payable(action.promise_id) if action.promise_id else None
    if payable is None or payable.msme_deadline is None or not base.in_scope(
            mandate, workspace_id=payable.workspace_id, counterparty_key=payable.counterparty_key,
            currency=payable.currency):
        return []
    run = action.run_date or snapshot.as_of
    inside = run <= payable.msme_deadline
    return [base.check(f"limit:{KEY}", "Inside the MSMED Act limit", inside, on_fail=v.CHECK_APPROVAL,
                       detail=f"Paid on {money.day(run)}, by the legal deadline of {money.day(payable.msme_deadline)}."
                       if inside else f"Paid on {money.day(run)}, after the legal deadline of "
                       f"{money.day(payable.msme_deadline)}. {CONSEQUENCE} A person approves it.",
                       evidence={"msme_deadline": payable.msme_deadline.isoformat(),
                                 "explanation": payable.msme_explanation})]


def _forecast(snapshot: Snapshot, mandate: base.ActiveMandate, sim) -> list[base.Risk]:
    """Deadlines in the next 14 days, and the financial year's end (31 March) for 43B(h)."""
    params: Params = mandate.params
    out: list[base.Risk] = []
    year_end = date(snapshot.as_of.year + (1 if snapshot.as_of.month > 3 else 0), 3, 31)
    for payable in base.payables_in_scope(snapshot, mandate):
        due = deadline_of(payable, params)
        if due is None or payable.settlement_state in (v.SETTLEMENT_SCHEDULED, v.SETTLEMENT_APPROVED):
            continue
        if 0 <= (due - snapshot.as_of).days <= 14:
            out.append(base.Risk(
                mandate=KEY, on=due, currency=payable.currency, amount_micros=payable.amount_micros,
                summary=(f"{payable.document} from {payable.counterparty_name or 'an MSME supplier'} must be paid by "
                         f"{money.day(due)} under the MSMED Act.")))
        if due <= year_end < due + timedelta(days=60) and (year_end - snapshot.as_of).days <= 45:
            out.append(base.Risk(
                mandate=KEY, on=year_end, currency=payable.currency, amount_micros=payable.amount_micros,
                summary=(f"If {payable.document} is still unpaid on {money.day(year_end)}, its deduction for this "
                         "financial year is lost (section 43B(h)).")))
    return out



def _backtest(history, mandate: base.ActiveMandate) -> list[base.Finding]:
    out: list[base.Finding] = []
    for payable in base.payables_in_scope(history.snapshot, mandate):
        deadline = payable.msme_deadline
        if deadline is None or not history.within(deadline):
            continue
        if payable.paid_on is not None and payable.paid_on > deadline:
            honesty, what = v.HONESTY_CONFIRMED, f"was paid {(payable.paid_on - deadline).days} days after"
        elif payable.paid_on is None and payable.open and deadline < history.end:
            honesty, what = v.HONESTY_LIKELY, "is still unpaid after"
        else:
            continue
        out.append(base.Finding(
            mandate=KEY, on=deadline, action=v.ACTION_SCHEDULE_PAYMENT, honesty=honesty,
            category=base.CATEGORY_MSME_LATE, currency=payable.currency, value_micros=0,
            summary=(f"{payable.document} from {payable.counterparty_name or 'an MSME supplier'} "
                     f"({money.say(payable.amount_micros, payable.currency)}) {what} the MSMED Act deadline of "
                     f"{money.day(deadline)}. " + CONSEQUENCE),
            evidence=base.evidence_of(payable) + [{"explanation": payable.msme_explanation}]))
    return out


TEMPLATE = base.Template(
    key=KEY, kind=v.MANDATE_KIND_LIMIT, title="Pay micro and small suppliers within the legal limit",
    group=base.GROUP_INDIA, order=8,
    summary="MSMED Act section 15 and income-tax section 43B(h): a hard deadline for the plan.", Params=Params,
    readback=_readback, evaluate=_evaluate, guard_checks=_guard, optimizer=_optimizer, forecast=_forecast, backtest=_backtest,
    law_links=rules.LAW_LINKS, disclaimer=rules.NOT_ADVICE, is_limit=True,
    keywords=("msme", "msmed", "micro", "small enterprise", "udyam", "43b", "43b(h)", "legal limit", "45 days",
              "15 days", "small suppliers"),
)
