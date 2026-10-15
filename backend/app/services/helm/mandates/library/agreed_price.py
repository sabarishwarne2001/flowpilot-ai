"""Library mandate 1 — "Never pay above the agreed price".

For any invoice line above its PRICE_AGREED promise (the purchase order's unit price, from three-way
matching) beyond a tolerance, Helm holds the invoice and drafts a dispute with the evidence. The
tolerance defaults to 0%; with "use matching's tolerance" on, the workspace's published matching
price tolerance is allowed as well, so Helm and the matching queue never disagree about a variance.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.helm import money
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec
from app.services.helm.snapshot import Payable, PriceBreach, Snapshot

KEY = "never_pay_above_agreed_price"


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tolerance_pct: Decimal = Field(default=Decimal("0"), ge=0, le=25)
    use_matching_tolerance: bool = True


def allowed_above(breach: PriceBreach, params: Params) -> int:
    pct = params.tolerance_pct
    if params.use_matching_tolerance:
        pct = max(pct, Decimal(breach.tolerance_bps) / Decimal(100))
    return money.pct_of(breach.agreed_micros, pct)


def breaches(payable: Payable, params: Params) -> list[PriceBreach]:
    return [b for b in payable.price_breaches if b.above_micros > allowed_above(b, params)]


def overcharge(payable: Payable, params: Params) -> int:
    return sum(b.overcharge_micros for b in breaches(payable, params))


def _readback(spec: MandateSpec, params: Params) -> base.Readback:
    tolerance = base.Chip("tolerance_pct", str(params.tolerance_pct), f"{params.tolerance_pct.normalize()}%",
                          "percent")
    matching = base.Chip("use_matching_tolerance", params.use_matching_tolerance,
                         "plus the matching tolerance" if params.use_matching_tolerance else "and nothing more",
                         "bool")
    return base.readback("Never pay an invoice line more than ", tolerance, " above the agreed price, ", matching,
                         ". Hold the invoice and draft a dispute with the evidence.")


def _evaluate(snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> base.Drift:
    params: Params = mandate.params
    offending = [p for p in base.payables_in_scope(snapshot, mandate) if p.open and breaches(p, params)]
    exposed = [p for p in offending if p.settlement_state not in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED)]
    paying = [p for p in exposed if p.settlement_state in (v.SETTLEMENT_SCHEDULED, v.SETTLEMENT_APPROVED)]
    held_total = sum(overcharge(p, params) for p in offending if p not in exposed)
    items = [{"promise_id": p.promise_id, "work_item_id": p.work_item_id, "document": p.document,
              "counterparty": p.counterparty_name, "currency": p.currency,
              "overcharge_micros": overcharge(p, params), "settlement": p.settlement_state} for p in offending]
    currency = offending[0].currency if offending else None
    if paying:
        return base.Drift(base.BREACHED, f"{len(paying)} overcharged invoice(s) scheduled to pay",
                          "An invoice billed above the agreed price is on a payment run.", len(paying), items)
    if exposed:
        total = sum(overcharge(p, params) for p in exposed)
        return base.Drift(base.DRIFTING, f"{money.say(total, currency)} above agreed prices",
                          f"{len(exposed)} invoice(s) bill above the agreed price and are not held yet.",
                          float(total) / money.MICROS, items)
    figure = f"{money.say(held_total, currency)} held" if held_total else "No overcharges"
    return base.Drift(base.HOLDING, figure, "Every invoice line is at or below its agreed price, or held.",
                      float(held_total) / money.MICROS, items)


def _explain(payable: Payable, params: Params) -> str:
    worst = max(breaches(payable, params), key=lambda b: b.above_micros)
    return (f"{payable.document} bills {worst.description} at {money.say(worst.billed_micros, payable.currency)} "
            f"a unit against the agreed {money.say(worst.agreed_micros, payable.currency)}"
            f"{' on ' + worst.po_number if worst.po_number else ''}: "
            f"{money.say(overcharge(payable, params), payable.currency)} too much in all.")


def _propose(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[base.Candidate]:
    params: Params = mandate.params
    out: list[base.Candidate] = []
    for payable in base.payables_in_scope(snapshot, mandate):
        if not payable.open or not breaches(payable, params):
            continue
        if payable.settlement_state in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED):
            continue
        value = overcharge(payable, params)
        out.append(base.Candidate(
            kind=v.ACTION_HOLD_PAYMENT, mandate=mandate,
            title=f"Hold {payable.document_number or payable.document} from {payable.counterparty_name or 'supplier'}",
            explanation=_explain(payable, params) + " Helm holds the invoice until it is corrected or credited.",
            key_parts=(payable.promise_id, "price"), workspace_id=payable.workspace_id,
            work_item_id=payable.work_item_id, promise_id=payable.promise_id,
            counterparty_key=payable.counterparty_key, amount_micros=payable.amount_micros,
            currency=payable.currency, value_micros=value,
            params={"reason": "PRICE_ABOVE_AGREED", "overcharge_micros": value},
        ))
        out.append(base.Candidate(
            kind=v.ACTION_DRAFT_DISPUTE, mandate=mandate,
            title=f"Draft a price dispute to {payable.counterparty_name or 'the supplier'}",
            explanation=_explain(payable, params) + " Helm drafts the email with the evidence; it is sent only "
            "in Act mode to a known contact.",
            key_parts=(payable.promise_id, "dispute"), workspace_id=payable.workspace_id,
            work_item_id=payable.work_item_id, promise_id=payable.promise_id,
            counterparty_key=payable.counterparty_key, amount_micros=value, currency=payable.currency,
            value_micros=value,
            params={"reason": "PRICE_ABOVE_AGREED", "lines": [
                {"description": b.description, "agreed_micros": b.agreed_micros, "billed_micros": b.billed_micros,
                 "overcharge_micros": b.overcharge_micros, "po_number": b.po_number}
                for b in breaches(payable, params)]},
        ))
    return out


def _guard(action: Any, snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> list[base.Check]:
    if action.kind not in (v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT):
        return []
    payable = snapshot.payable(action.promise_id) if action.promise_id else None
    if payable is None or not base.in_scope(mandate, workspace_id=payable.workspace_id,
                                            counterparty_key=payable.counterparty_key, currency=payable.currency):
        return []
    found = breaches(payable, mandate.params)
    return [base.check(
        f"limit:{KEY}", "Within the agreed price", not found,
        detail=("Every invoice line is at or below its agreed price." if not found else _explain(payable,
                                                                                                 mandate.params)),
        evidence={"lines": [{"description": b.description, "agreed_micros": b.agreed_micros,
                             "billed_micros": b.billed_micros} for b in found]})]


def _optimizer(snapshot: Snapshot, mandate: base.ActiveMandate) -> base.OptimizerTerms:
    terms = base.OptimizerTerms()
    for payable in base.payables_in_scope(snapshot, mandate):
        if payable.open and breaches(payable, mandate.params):
            terms.exclude[payable.promise_id] = "billed above the agreed price"
    return terms


TEMPLATE = base.Template(
    key=KEY, kind=v.MANDATE_KIND_LIMIT, title="Never pay above the agreed price", group=base.GROUP_PROTECT,
    order=1, summary="Hold any invoice billed above the agreed price, and draft a dispute with the evidence.",
    Params=Params, readback=_readback, evaluate=_evaluate, propose=_propose, guard_checks=_guard,
    optimizer=_optimizer, is_limit=True,
    keywords=("agreed price", "above the price", "overcharge", "over charge", "price", "po price", "contract rate",
              "overbill", "more than agreed"),
)
