"""The Guard: a pure, deterministic check of one action against everything that must hold.

    guard(action, snapshot, mandates, actor, context) -> GuardResult(passed, checks)

It runs when the Planner proposes an action and again when the action is about to execute; if the
state changed and a check now fails, execution is blocked and the book is re-planned. Everything it
needs from the database is read BEFORE the call and passed in (`Actor`, `GuardContext`), so the
function itself has no session, no clock and no network: the same inputs give the same verdict,
which is what a certificate can then prove.

Each check has an id, a plain title, a result (pass, fail, needs_approval, not_applicable), a detail
sentence and its evidence. `passed` means no check failed; an action that needs approval waits for a
person; only an action that passed with nothing needing approval may run on its own (Act mode,
within its cap).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional, Sequence

from app.services.helm import money
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.registry import TEMPLATES
from app.services.helm.snapshot import Snapshot

#: Who may approve or execute which actions (brief §5.4). SYSTEM is the Planner / an Act-mode run.
APPROVER_ROLES = {"OWNER", "ADMIN"}
PAYMENT_APPROVER_ROLES = {"OWNER", "ADMIN", "BILLING"}
FACT_ACTIONS = (v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT, v.ACTION_SEND_REMINDER, v.ACTION_DRAFT_DISPUTE,
                v.ACTION_REQUEST_DOCUMENT)
PAYING_ACTIONS = (v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT)
OUTBOUND_ACTIONS = (v.ACTION_SEND_REMINDER, v.ACTION_DRAFT_DISPUTE, v.ACTION_REQUEST_DOCUMENT, v.ACTION_WEBHOOK)


@dataclass(frozen=True)
class ActionView:
    """What the Guard reads of an action (a proposal or a stored row)."""

    kind: str
    idempotency_key: str
    mandate_id: Optional[str] = None
    mandate_version: Optional[int] = None
    workspace_id: Optional[str] = None
    work_item_id: Optional[str] = None
    promise_id: Optional[str] = None
    counterparty_key: Optional[str] = None
    amount_micros: Optional[int] = None
    currency: Optional[str] = None
    run_date: Optional[date] = None
    title: str = ""
    params: dict = field(default_factory=dict)

    @classmethod
    def of(cls, candidate: base.Candidate) -> "ActionView":
        return cls(kind=candidate.kind, idempotency_key=candidate.idempotency_key,
                   mandate_id=candidate.mandate.id if candidate.mandate else None,
                   mandate_version=candidate.mandate.version if candidate.mandate else None,
                   workspace_id=candidate.workspace_id, work_item_id=candidate.work_item_id,
                   promise_id=candidate.promise_id, counterparty_key=candidate.counterparty_key,
                   amount_micros=candidate.amount_micros, currency=candidate.currency, run_date=candidate.run_date,
                   title=candidate.title, params=dict(candidate.params))

    def as_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "idempotency_key": self.idempotency_key, "mandate_id": self.mandate_id,
                "mandate_version": self.mandate_version, "workspace_id": self.workspace_id,
                "work_item_id": self.work_item_id, "promise_id": self.promise_id,
                "counterparty_key": self.counterparty_key, "amount_micros": self.amount_micros,
                "currency": self.currency, "run_date": self.run_date.isoformat() if self.run_date else None,
                "title": self.title}


@dataclass(frozen=True)
class Actor:
    user_id: Optional[str]
    role: str  # OWNER | ADMIN | BILLING | MEMBER | SYSTEM
    can_read_workspace: bool = True

    @property
    def is_system(self) -> bool:
        return self.role == "SYSTEM"


SYSTEM = Actor(user_id=None, role="SYSTEM")


@dataclass(frozen=True)
class GuardContext:
    stage: str = v.CERT_STAGE_PLAN
    capability_held: bool = True
    #: True / False when an outbound destination was checked against egress; None: not applicable.
    destination_allowed: Optional[bool] = None
    destination_detail: str = ""
    #: Has this idempotency key already run (EXECUTED, REJECTED or UNDONE)?
    already_ran: bool = False
    #: Payments the plan makes before this one, per currency (micros).
    scheduled_before: dict = field(default_factory=dict)
    #: The cautious (P10) cumulative inflow up to this action's run day, per currency (micros).
    inflow_by: dict = field(default_factory=dict)
    sim: Optional[dict] = None
    schedule: dict = field(default_factory=dict)


@dataclass
class GuardResult:
    passed: bool
    needs_approval: bool
    checks: list[base.Check]

    @property
    def can_run_alone(self) -> bool:
        return self.passed and not self.needs_approval

    @property
    def failed(self) -> list[base.Check]:
        return [c for c in self.checks if c.result == v.CHECK_FAIL]

    def as_json(self) -> dict[str, Any]:
        return {"passed": self.passed, "needs_approval": self.needs_approval,
                "checks": [c.as_json() for c in self.checks]}


def _skip(id: str, title: str, detail: str) -> base.Check:
    return base.Check(id=id, title=title, result=v.CHECK_SKIP, detail=detail)


def guard(action: ActionView, snapshot: Snapshot, mandates: Sequence[base.ActiveMandate], actor: Actor,
          context: GuardContext) -> GuardResult:
    checks: list[base.Check] = []
    payable = snapshot.payable(action.promise_id) if action.promise_id else None
    if payable is None and action.work_item_id:
        payable = snapshot.payable_for_work_item(action.work_item_id)
    own = next((m for m in mandates if m.id == action.mandate_id), None)
    active = [m for m in mandates if m.status == v.MANDATE_ACTIVE]

    # scope -----------------------------------------------------------------------------------
    if own is None:
        checks.append(_skip("scope", "In the mandate's scope", "This action is not tied to one mandate."))
    else:
        inside = base.in_scope(own, workspace_id=action.workspace_id, counterparty_key=action.counterparty_key,
                               currency=action.currency)
        checks.append(base.check("scope", "In the mandate's scope", inside,
                                 detail=(f"Inside the scope of \"{own.title}\"." if inside else
                                         f"Outside the scope of \"{own.title}\" (its workspaces, counterparties or "
                                         "currency).")))

    # every active limit that applies -----------------------------------------------------------
    ctx = {"sim": context.sim, "schedule": context.schedule, "scheduled_before": context.scheduled_before,
           "inflow_by": context.inflow_by}
    for mandate in active:
        template = TEMPLATES.get(mandate.template)
        if template is None or not template.is_limit:
            continue
        checks.extend(template.guard_checks(action, snapshot, mandate, ctx))

    # facts ------------------------------------------------------------------------------------
    if action.kind in FACT_ACTIONS and payable is not None:
        trusted = payable.confidence in v.TRUSTED_CONFIDENCES
        checks.append(base.check(
            "facts", "Facts are verified", trusted, on_fail=v.CHECK_APPROVAL,
            detail=("The invoice's amount, due date and payee were verified."
                    if payable.confidence == v.CONFIDENCE_VERIFIED else
                    "The invoice's facts meet the calibrated-autonomy threshold."
                    if payable.confidence == v.CONFIDENCE_CALIBRATED else
                    "The invoice's facts were read by the model and not yet checked, so a person approves."),
            evidence={"confidence": payable.confidence, "work_item_id": payable.work_item_id}))
    else:
        checks.append(_skip("facts", "Facts are verified", "This action relies on no extracted invoice fact."))

    # conflicts --------------------------------------------------------------------------------
    if payable is not None and action.kind in PAYING_ACTIONS:
        conflicts = [f for f in payable.flags if f.kind == "CONFLICT"]
        checks.append(base.check(
            "conflicts", "No open conflicts", not conflicts, on_fail=v.CHECK_APPROVAL,
            detail="No open TruthMesh or corroborator conflict on this document." if not conflicts else
            f"{len(conflicts)} open conflict(s): {conflicts[0].summary}. A person decides.",
            evidence={"conflicts": [f.id for f in conflicts]}))
    else:
        checks.append(_skip("conflicts", "No open conflicts", "Not a payment."))

    # fraud and duplicates ---------------------------------------------------------------------
    if payable is not None and action.kind in PAYING_ACTIONS:
        bank = payable.flag("BANK_ACCOUNT_CHANGED")
        duplicate = next((f for f in payable.flags if f.kind == "DUPLICATE" and f.strong), None)
        bad = bank or duplicate
        checks.append(base.check(
            "fraud", "No fraud or duplicate flag", bad is None,
            detail="No open bank-account change and no probable duplicate." if bad is None else
            (f"The supplier's bank account changed ({payable.bank_account or 'masked'}) and is not verified yet."
             if bank else f"Probable duplicate: {duplicate.summary}."),
            evidence={"flags": [f.id for f in (bank, duplicate) if f]}))
    else:
        checks.append(_skip("fraud", "No fraud or duplicate flag", "Not a payment."))

    # price ------------------------------------------------------------------------------------
    if payable is not None and action.kind in PAYING_ACTIONS:
        above = [b for b in payable.price_breaches if b.above_micros > money.pct_of(b.agreed_micros,
                                                                                   _pct(b.tolerance_bps))]
        checks.append(base.check(
            "price", "Within the agreed price", not above, on_fail=v.CHECK_APPROVAL,
            detail="Every invoice line is within the agreed price and the matching tolerance." if not above else
            f"{len(above)} line(s) above the agreed price; a person decides.",
            evidence={"lines": [b.description for b in above]}))
    else:
        checks.append(_skip("price", "Within the agreed price", "Not a payment."))

    # account ----------------------------------------------------------------------------------
    checks.append(base.check("account", "The organization is writable", snapshot.account_writable,
                             detail="The organization is active and paid up." if snapshot.account_writable else
                             f"The organization cannot act now: {snapshot.account_reason}."))

    # permissions ------------------------------------------------------------------------------
    if not context.capability_held:
        checks.append(base.Check("permission", "Allowed for this person and plan", v.CHECK_FAIL,
                                 "The organization's plan does not include Helm (Business and Enterprise)."))
    elif actor.is_system:
        checks.append(base.Check("permission", "Allowed for this person and plan", v.CHECK_PASS,
                                 "Helm proposes; the plan includes Helm."))
    else:
        allowed = PAYMENT_APPROVER_ROLES if action.kind in v.PAYMENT_ACTIONS else APPROVER_ROLES
        ok = actor.role in allowed and actor.can_read_workspace
        why = ("Allowed." if ok else
               "Only organization owners and admins approve this action." if actor.role not in allowed else
               "You cannot read this document's workspace, so you cannot approve it; ask an owner for access.")
        checks.append(base.check("permission", "Allowed for this person and plan", ok, detail=why,
                                 evidence={"role": actor.role}))

    # outbound destinations -------------------------------------------------------------------
    if action.kind in OUTBOUND_ACTIONS:
        if context.destination_allowed is None:
            checks.append(base.Check("destination", "The destination is allowed", v.CHECK_APPROVAL,
                                     context.destination_detail or "No destination was checked yet."))
        else:
            checks.append(base.check("destination", "The destination is allowed", context.destination_allowed,
                                     detail=context.destination_detail or ("Allowed." if context.destination_allowed
                                                                           else "Refused.")))
    else:
        checks.append(_skip("destination", "The destination is allowed", "Nothing leaves the platform."))

    # idempotency ------------------------------------------------------------------------------
    already = context.already_ran or action.idempotency_key in snapshot.settled_action_keys
    checks.append(base.check("idempotency", "Has not already run", not already,
                             detail="This action has not run before." if not already else
                             "This exact action already ran (or was rejected or undone)."))

    # cash, where no floor mandate covers the currency -----------------------------------------
    floors = {m.params.floor.currency for m in active if m.template == "keep_cash_above"}
    if action.kind in PAYING_ACTIONS and action.currency and action.currency not in floors:
        cash = snapshot.cash.get(action.currency)
        if cash is None:
            checks.append(_skip("cash", "Cash stays above the floor",
                                f"No {action.currency} cash position and no floor mandate."))
        else:
            after = (cash.amount_micros + int(context.inflow_by.get(action.currency, 0))
                     - int(context.scheduled_before.get(action.currency, 0)) - int(action.amount_micros or 0))
            checks.append(base.check("cash", "Cash stays above the floor", after >= 0, on_fail=v.CHECK_APPROVAL,
                                     detail=f"Cash after this payment on the cautious path: "
                                            f"{money.say(after, action.currency)}.",
                                     evidence={"after_micros": after}))
    elif action.kind not in PAYING_ACTIONS:
        checks.append(_skip("cash", "Cash stays above the floor", "Not a payment."))

    # autonomy --------------------------------------------------------------------------------
    if own is None:
        checks.append(base.Check("autonomy", "Allowed to run without a person", v.CHECK_APPROVAL,
                                 "Helm's own suggestion: a person approves."))
    elif own.mode == v.AUTONOMY_ACT:
        cap = own.spec.autonomy.act_cap
        cap_amount = cap.amount_micros if cap else 0
        if action.kind in PAYING_ACTIONS:
            amount = int(action.amount_micros or 0)
            within = (cap is None or action.currency in (None, cap.currency)) and amount <= cap_amount
            detail = (f"Act mode, within the cap of {money.say(cap_amount, action.currency)}." if within else
                      f"Act mode, but {money.say(amount, action.currency)} is above the act cap of "
                      f"{money.say(cap_amount, action.currency)} (the default is 0): a person approves.")
        else:
            within = True
            detail = "Act mode: no money moves, so Helm may do this on its own."
        checks.append(base.check("autonomy", "Allowed to run without a person", within, on_fail=v.CHECK_APPROVAL,
                                 detail=detail, evidence={"act_cap_micros": cap_amount}))
    else:
        checks.append(base.Check("autonomy", "Allowed to run without a person", v.CHECK_APPROVAL,
                                 "Propose mode: a person approves." if own.mode == v.AUTONOMY_PROPOSE else
                                 "Watch mode: Helm only reports."))

    passed = not any(c.result == v.CHECK_FAIL for c in checks)
    needs = any(c.result == v.CHECK_APPROVAL for c in checks)
    return GuardResult(passed=passed, needs_approval=needs, checks=checks)


def _pct(bps: int):
    from decimal import Decimal

    return Decimal(bps) / Decimal(100)


__all__ = ["APPROVER_ROLES", "ActionView", "Actor", "GuardContext", "GuardResult", "PAYMENT_APPROVER_ROLES",
           "SYSTEM", "guard"]
