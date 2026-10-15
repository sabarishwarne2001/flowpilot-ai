"""Library mandate 4 — "Every invoice above an amount has a matching PO".

Above the threshold (in its currency), an invoice that three-way matching has not paired with a
purchase order is held and a review task is opened: find the PO, or decide to pay without one. The
hold is released once a match exists (or the invoice is no longer above the amount, after a credit
note). Invoices in other currencies are outside this mandate; the readback says which currency.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.services.helm import money
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec, Money
from app.services.helm.snapshot import Payable, Snapshot

KEY = "po_required_above"
CODE = "NO_MATCHED_PO"


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: Money


def missing(payable: Payable, params: Params) -> bool:
    return (payable.open and payable.currency == params.threshold.currency
            and payable.amount_micros > params.threshold.amount_micros and not payable.has_matched_po
            and CODE not in payable.waived)


def _readback(spec: MandateSpec, params: Params) -> base.Readback:
    threshold = base.Chip("threshold", params.threshold.model_dump(),
                          money.say(params.threshold.amount_micros, params.threshold.currency), "money")
    return base.readback("Every invoice above ", threshold, " has a matching purchase order; hold any that does not "
                         "and open a review task.")


def _evaluate(snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> base.Drift:
    params: Params = mandate.params
    without = [p for p in base.payables_in_scope(snapshot, mandate) if missing(p, params)]
    exposed = [p for p in without if p.settlement_state not in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED)]
    paying = [p for p in exposed if p.settlement_state in (v.SETTLEMENT_SCHEDULED, v.SETTLEMENT_APPROVED)]
    items = [{"promise_id": p.promise_id, "work_item_id": p.work_item_id, "document": p.document,
              "counterparty": p.counterparty_name, "currency": p.currency, "amount_micros": p.amount_micros,
              "po_reference": p.po_reference} for p in without]
    if paying:
        return base.Drift(base.BREACHED, f"{len(paying)} without a PO on a payment run",
                          "An invoice above the amount is scheduled without a matching purchase order.", len(paying),
                          items)
    if exposed:
        return base.Drift(base.DRIFTING, f"{len(exposed)} without a matching PO",
                          f"{len(exposed)} invoice(s) above {money.say(params.threshold.amount_micros, params.threshold.currency)} "
                          "have no matching purchase order and are not held yet.", len(exposed), items)
    return base.Drift(base.HOLDING, f"{len(without)} held for a PO" if without else "Every large invoice has a PO",
                      "No invoice above the amount can be paid without a matching purchase order.", len(without),
                      items)


def _propose(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[base.Candidate]:
    params: Params = mandate.params
    out: list[base.Candidate] = []
    limit = money.say(params.threshold.amount_micros, params.threshold.currency)
    for payable in base.payables_in_scope(snapshot, mandate):
        if not missing(payable, params) or payable.settlement_state in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED):
            continue
        quoted = f" It quotes PO {payable.po_reference}, but no such purchase order was matched." \
            if payable.po_reference else " It quotes no PO number."
        out.append(base.Candidate(
            kind=v.ACTION_HOLD_PAYMENT, mandate=mandate,
            title=f"Hold {payable.document_number or payable.document}: no matching PO",
            explanation=(f"{payable.document} is for {money.say(payable.amount_micros, payable.currency)}, above "
                         f"{limit}, and has no matching purchase order.{quoted} Helm holds it until one is found "
                         "or a person decides."),
            key_parts=(payable.promise_id, "po", params.threshold.amount_micros), workspace_id=payable.workspace_id,
            work_item_id=payable.work_item_id, promise_id=payable.promise_id,
            counterparty_key=payable.counterparty_key, amount_micros=payable.amount_micros,
            currency=payable.currency, params={"reason": CODE}))
        out.append(base.Candidate(
            kind=v.ACTION_CREATE_TASK, mandate=mandate,
            title=f"Find the purchase order for {payable.document_number or payable.document}",
            explanation=(f"{payable.document} from {payable.counterparty_name or 'the supplier'} is above {limit} "
                         f"and has no matching purchase order.{quoted} Upload or match the PO, or settle this task "
                         "DONE to pay without one (Helm then releases the hold)."),
            key_parts=(payable.promise_id, "po-task", params.threshold.amount_micros),
            workspace_id=payable.workspace_id, work_item_id=payable.work_item_id, promise_id=payable.promise_id,
            counterparty_key=payable.counterparty_key, amount_micros=payable.amount_micros,
            currency=payable.currency, params={"task_kind": v.TASK_REVIEW_PO, "severity": "MEDIUM"}))
    return out


def _guard(action: Any, snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> list[base.Check]:
    if action.kind not in (v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT):
        return []
    payable = snapshot.payable(action.promise_id) if action.promise_id else None
    if payable is None or not base.in_scope(mandate, workspace_id=payable.workspace_id,
                                            counterparty_key=payable.counterparty_key, currency=payable.currency):
        return []
    params: Params = mandate.params
    bad = missing(payable, params)
    return [base.check(f"limit:{KEY}", "Has a matching purchase order", not bad,
                       detail="A purchase order is matched, or the invoice is not above the amount." if not bad else
                       f"Above {money.say(params.threshold.amount_micros, params.threshold.currency)} with no "
                       "matching purchase order.")]


def _optimizer(snapshot: Snapshot, mandate: base.ActiveMandate) -> base.OptimizerTerms:
    terms = base.OptimizerTerms()
    for payable in base.payables_in_scope(snapshot, mandate):
        if missing(payable, mandate.params):
            terms.exclude[payable.promise_id] = "no matching purchase order"
    return terms


def _still_holds(snapshot: Snapshot, payable: Payable, mandate: base.ActiveMandate) -> bool:
    return missing(payable, mandate.params)



def _backtest(history, mandate: base.ActiveMandate) -> list[base.Finding]:
    params: Params = mandate.params
    out: list[base.Finding] = []
    for payable in base.payables_in_scope(history.snapshot, mandate):
        on = base.seen_on(payable)
        unpaid_ok = payable.open or payable.settlement_state == v.SETTLEMENT_PAID
        if not unpaid_ok or not history.within(on) or payable.currency != params.threshold.currency \
                or payable.amount_micros <= params.threshold.amount_micros or payable.has_matched_po:
            continue
        paid = "was paid" if payable.settlement_state == v.SETTLEMENT_PAID else "arrived"
        out.append(base.Finding(
            mandate=KEY, on=on, action=v.ACTION_HOLD_PAYMENT, honesty=v.HONESTY_CONFIRMED,
            category=base.CATEGORY_CONTROL, currency=payable.currency, value_micros=0,
            summary=(f"{payable.document} ({money.say(payable.amount_micros, payable.currency)}) {paid} without a "
                     "matching purchase order; Helm would have held it and asked for the PO."),
            evidence=base.evidence_of(payable)))
    return out


TEMPLATE = base.Template(
    key=KEY, kind=v.MANDATE_KIND_LIMIT, title="Every invoice above an amount has a matching PO",
    group=base.GROUP_PROTECT, order=4,
    summary="Above the amount, an invoice without a matched purchase order is held and a review task opened.",
    Params=Params, readback=_readback, evaluate=_evaluate, propose=_propose, guard_checks=_guard,
    optimizer=_optimizer, is_limit=True, hold_code=CODE, backtest=_backtest, still_holds=_still_holds,
    keywords=("purchase order", "po", "without a po", "no po", "matching po", "three-way", "3-way", "above"),
)
