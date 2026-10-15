"""Library mandate 2 — "Never pay a duplicate".

A document the radar flags as a probable duplicate (an open `DUPLICATE_DOCUMENT` finding on layers
L0–L2: the same file, the same supplier and number, or near-identical text) cannot be scheduled
until a reviewer clears the finding in the review hub. "Similar-looking" findings (layer L3,
embedding similarity) hold too only when the parameter says so: similarity is evidence, not proof.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.services.helm import money
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec
from app.services.helm.snapshot import Payable, Snapshot

KEY = "never_pay_duplicate"


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_similar: bool = False


def duplicate_flag(payable: Payable, params: Params):
    flag = payable.flag("DUPLICATE")
    if flag is None:
        return None
    if not flag.strong and not params.include_similar:
        return None
    return flag


def _readback(spec: MandateSpec, params: Params) -> base.Readback:
    similar = base.Chip("include_similar", params.include_similar,
                        "including documents that only look similar" if params.include_similar
                        else "when the radar finds a probable duplicate", "bool")
    return base.readback("Never pay a duplicate invoice: hold it ", similar,
                         ", until a reviewer clears it.")


def _evaluate(snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> base.Drift:
    params: Params = mandate.params
    flagged = [p for p in base.payables_in_scope(snapshot, mandate) if p.open and duplicate_flag(p, params)]
    exposed = [p for p in flagged if p.settlement_state not in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED)]
    paying = [p for p in exposed if p.settlement_state in (v.SETTLEMENT_SCHEDULED, v.SETTLEMENT_APPROVED)]
    items = [{"promise_id": p.promise_id, "work_item_id": p.work_item_id, "document": p.document,
              "counterparty": p.counterparty_name, "currency": p.currency, "amount_micros": p.amount_micros,
              "finding": duplicate_flag(p, params).summary} for p in flagged]
    currency = flagged[0].currency if flagged else None
    if paying:
        return base.Drift(base.BREACHED, f"{len(paying)} probable duplicate(s) on a payment run",
                          "A probable duplicate is scheduled to be paid.", len(paying), items)
    if exposed:
        total = sum(p.amount_micros for p in exposed)
        return base.Drift(base.DRIFTING, f"{money.say(total, currency)} in probable duplicates",
                          f"{len(exposed)} probable duplicate(s) are not held yet.", float(total) / money.MICROS,
                          items)
    held = sum(p.amount_micros for p in flagged)
    return base.Drift(base.HOLDING, f"{money.say(held, currency)} held" if held else "No duplicates",
                      "No probable duplicate can be paid.", float(held) / money.MICROS, items)


def _propose(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[base.Candidate]:
    out = []
    for payable in base.payables_in_scope(snapshot, mandate):
        flag = duplicate_flag(payable, mandate.params) if payable.open else None
        if flag is None or payable.settlement_state in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED):
            continue
        out.append(base.Candidate(
            kind=v.ACTION_HOLD_PAYMENT, mandate=mandate,
            title=f"Hold {payable.document_number or payable.document}: probable duplicate",
            explanation=(f"The radar found that {payable.document} is a probable duplicate ({flag.summary}). "
                         "Helm holds it so it cannot be paid twice; a reviewer clears or confirms the finding "
                         "in the review hub."),
            key_parts=(payable.promise_id, "duplicate", flag.id), workspace_id=payable.workspace_id,
            work_item_id=payable.work_item_id, promise_id=payable.promise_id,
            counterparty_key=payable.counterparty_key, amount_micros=payable.amount_micros,
            currency=payable.currency, value_micros=payable.amount_micros,
            params={"reason": "PROBABLE_DUPLICATE", "finding_id": flag.id},
        ))
    return out


def _guard(action: Any, snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> list[base.Check]:
    if action.kind not in (v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT):
        return []
    payable = snapshot.payable(action.promise_id) if action.promise_id else None
    if payable is None or not base.in_scope(mandate, workspace_id=payable.workspace_id,
                                            counterparty_key=payable.counterparty_key, currency=payable.currency):
        return []
    flag = duplicate_flag(payable, mandate.params)
    return [base.check(f"limit:{KEY}", "Not a probable duplicate", flag is None,
                       detail="No open duplicate finding." if flag is None else
                       f"Open duplicate finding: {flag.summary}", evidence={"finding_id": flag.id} if flag else {})]


def _optimizer(snapshot: Snapshot, mandate: base.ActiveMandate) -> base.OptimizerTerms:
    terms = base.OptimizerTerms()
    for payable in base.payables_in_scope(snapshot, mandate):
        if payable.open and duplicate_flag(payable, mandate.params):
            terms.exclude[payable.promise_id] = "probable duplicate"
    return terms


TEMPLATE = base.Template(
    key=KEY, kind=v.MANDATE_KIND_LIMIT, title="Never pay a duplicate", group=base.GROUP_PROTECT, order=2,
    summary="A probable duplicate cannot be scheduled until a reviewer clears it.", Params=Params,
    readback=_readback, evaluate=_evaluate, propose=_propose, guard_checks=_guard, optimizer=_optimizer,
    is_limit=True, keywords=("duplicate", "twice", "double pay", "paid twice", "same invoice"),
)
