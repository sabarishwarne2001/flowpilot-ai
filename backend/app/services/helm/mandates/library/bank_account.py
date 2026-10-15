"""Library mandate 3 — "Never pay a changed bank account until verified".

An open BANK_ACCOUNT_CHANGED flag (the radar saw this supplier ask to be paid into an account it
never used before) holds the invoice and opens a verification task in the review hub. The task says
how: confirm by phone, on a number you already had, never one printed on this invoice or in the
email that brought it. When the task is settled VERIFIED the flag closes and Helm proposes releasing
the hold; NOT_VERIFIED keeps it held.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.services.helm import money
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec
from app.services.helm.snapshot import Payable, Snapshot

KEY = "never_pay_changed_bank_account"
CODE = "BANK_ACCOUNT_CHANGED"

HOW_TO_VERIFY = ("Call the supplier on a phone number you already had before this invoice (from an earlier "
                 "invoice, the contract or your own records), never one printed on this invoice or in the email "
                 "that brought it, and confirm the new account number with them.")


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_task: bool = True


def changed(payable: Payable):
    return payable.flag(CODE) if payable.open else None


def _readback(spec: MandateSpec, params: Params) -> base.Readback:
    task = base.Chip("open_task", params.open_task,
                     "and open a verification task" if params.open_task else "without opening a task", "bool")
    return base.readback("Never pay an invoice whose bank account changed until a person verifies it by phone, ",
                         task, ".")


def _evaluate(snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> base.Drift:
    flagged = [p for p in base.payables_in_scope(snapshot, mandate) if changed(p)]
    exposed = [p for p in flagged if p.settlement_state not in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED)]
    paying = [p for p in exposed if p.settlement_state in (v.SETTLEMENT_SCHEDULED, v.SETTLEMENT_APPROVED)]
    items = [{"promise_id": p.promise_id, "work_item_id": p.work_item_id, "document": p.document,
              "counterparty": p.counterparty_name, "currency": p.currency, "amount_micros": p.amount_micros,
              "bank_account": p.bank_account, "flag": changed(p).summary} for p in flagged]
    if paying:
        return base.Drift(base.BREACHED, f"{len(paying)} unverified account(s) on a payment run",
                          "An invoice with an unverified new bank account is scheduled to be paid.", len(paying),
                          items)
    if exposed:
        total = sum(p.amount_micros for p in exposed)
        return base.Drift(base.DRIFTING, f"{money.say(total, exposed[0].currency)} to unverified accounts",
                          f"{len(exposed)} invoice(s) ask to be paid into a new account and are not held yet.",
                          float(total) / money.MICROS, items)
    return base.Drift(base.HOLDING, f"{len(flagged)} held until verified" if flagged else "No changed accounts",
                      "No invoice can be paid into an account nobody verified.", len(flagged), items)


def _propose(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[base.Candidate]:
    params: Params = mandate.params
    out: list[base.Candidate] = []
    for payable in base.payables_in_scope(snapshot, mandate):
        flag = changed(payable)
        if flag is None or payable.settlement_state in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED):
            continue
        name = payable.counterparty_name or "the supplier"
        account = f" ({payable.bank_account})" if payable.bank_account else ""
        out.append(base.Candidate(
            kind=v.ACTION_HOLD_PAYMENT, mandate=mandate,
            title=f"Hold {payable.document_number or payable.document}: new bank account",
            explanation=(f"{payable.document} asks to be paid into a bank account{account} that {name} never used "
                         f"before ({flag.summary}). Helm holds it until a person verifies the account."),
            key_parts=(payable.promise_id, "bank", flag.id), workspace_id=payable.workspace_id,
            work_item_id=payable.work_item_id, promise_id=payable.promise_id,
            counterparty_key=payable.counterparty_key, amount_micros=payable.amount_micros,
            currency=payable.currency, value_micros=payable.amount_micros,
            params={"reason": CODE, "flag_id": flag.id}))
        if params.open_task:
            out.append(base.Candidate(
                kind=v.ACTION_CREATE_TASK, mandate=mandate,
                title=f"Verify {name}'s new bank account before paying {payable.document_number or payable.document}",
                explanation=f"{payable.document} asks to be paid into a new account{account}. {HOW_TO_VERIFY} "
                            "Then settle this task VERIFIED or NOT_VERIFIED.",
                key_parts=(payable.promise_id, "verify-bank", flag.id), workspace_id=payable.workspace_id,
                work_item_id=payable.work_item_id, promise_id=payable.promise_id,
                counterparty_key=payable.counterparty_key, amount_micros=payable.amount_micros,
                currency=payable.currency,
                params={"task_kind": v.TASK_VERIFY_BANK_ACCOUNT, "severity": "HIGH", "flag_id": flag.id}))
    return out


def _guard(action: Any, snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> list[base.Check]:
    if action.kind not in (v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT, v.ACTION_RELEASE_HOLD):
        return []
    payable = snapshot.payable(action.promise_id) if action.promise_id else None
    if payable is None or not base.in_scope(mandate, workspace_id=payable.workspace_id,
                                            counterparty_key=payable.counterparty_key, currency=payable.currency):
        return []
    flag = changed(payable)
    return [base.check(f"limit:{KEY}", "Not paid into an unverified new account", flag is None,
                       detail="The bank account is the one this supplier used before, or it was verified."
                       if flag is None else f"{flag.summary} Nobody verified it yet.",
                       evidence={"flag_id": flag.id} if flag else {})]


def _optimizer(snapshot: Snapshot, mandate: base.ActiveMandate) -> base.OptimizerTerms:
    terms = base.OptimizerTerms()
    for payable in base.payables_in_scope(snapshot, mandate):
        if changed(payable):
            terms.exclude[payable.promise_id] = "new bank account not verified"
    return terms



def _backtest(history, mandate: base.ActiveMandate) -> list[base.Finding]:
    out: list[base.Finding] = []
    for payable in base.payables_in_scope(history.snapshot, mandate):
        for flag in history.flags_for(payable.work_item_id, CODE)[:1]:
            on = max(base.seen_on(payable) or flag.raised_on, flag.raised_on)
            if not history.within(on):
                continue
            if flag.status == "CONFIRMED":
                honesty, how, value = v.HONESTY_CONFIRMED, "a reviewer confirmed the change was not genuine", \
                    payable.amount_micros
            elif flag.status == "DISMISSED":
                honesty, how, value = v.HONESTY_CONFIRMED, "it was later verified", 0
            else:
                honesty, how, value = v.HONESTY_POTENTIAL, "nobody verified it", payable.amount_micros
            out.append(base.Finding(
                mandate=KEY, on=on, action=v.ACTION_HOLD_PAYMENT, honesty=honesty,
                category=base.CATEGORY_PAYMENT_PROTECTED, currency=payable.currency, value_micros=value,
                summary=(f"{payable.document} asked to be paid into a new bank account ({how}); Helm would have "
                         "held it and asked for a phone check first."),
                evidence=base.evidence_of(payable) + [{"flag": flag.summary}]))
    return out


TEMPLATE = base.Template(
    key=KEY, kind=v.MANDATE_KIND_LIMIT, title="Never pay a changed bank account until verified",
    group=base.GROUP_PROTECT, order=3,
    summary="Hold an invoice whose bank account changed and open a phone-verification task.", Params=Params,
    readback=_readback, evaluate=_evaluate, propose=_propose, guard_checks=_guard, optimizer=_optimizer,
    is_limit=True, hold_code=CODE, backtest=_backtest, still_holds=lambda snapshot, payable, mandate: changed(payable) is not None,
    keywords=("bank account", "bank details", "account changed", "new account", "verify", "payee", "fraud",
              "phishing", "iban"),
)
