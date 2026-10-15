"""Library mandate 9 — "Decide every renewal before its notice deadline".

A contract that renews unless notice is given (an ARCH-46 notice obligation) becomes a decision for
a person N days before its deadline: a task in the review hub (renew, cancel or renegotiate), for
the mandate's owner unless a contract owner is named. The decision is written on the obligation
(the obligation is then done), so the promise is kept and the task does not come back.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.helm import money
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec
from app.services.helm.snapshot import Notice, Snapshot

KEY = "decide_renewals"


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days_before: int = Field(default=30, ge=1, le=365)
    contract_owner_user_id: Optional[str] = Field(default=None, max_length=36)


def _open(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[Notice]:
    return [n for n in snapshot.notices if n.status == v.STATUS_OPEN and n.due_date is not None
            and base.in_scope(mandate, workspace_id=n.workspace_id, counterparty_key=None, currency=None)]


def _readback(spec: MandateSpec, params: Params) -> base.Readback:
    days = base.Chip("days_before", params.days_before, f"{params.days_before} days before", "days")
    return base.readback("Decide every contract renewal (renew, cancel or renegotiate) ", days,
                         " its notice deadline.")


def _evaluate(snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> base.Drift:
    params: Params = mandate.params
    notices = _open(snapshot, mandate)
    missed = [n for n in notices if n.due_date < snapshot.as_of]
    due = [n for n in notices if n not in missed and (n.due_date - snapshot.as_of).days <= params.days_before]
    items = [{"promise_id": n.promise_id, "obligation_id": n.obligation_id, "title": n.title,
              "counterparty": n.counterparty_name, "due_date": n.due_date.isoformat()} for n in notices]
    if missed:
        return base.Drift(base.BREACHED, f"{len(missed)} notice deadline(s) passed undecided",
                          "A contract's notice deadline passed with no decision recorded: it may have renewed "
                          "on its own.", len(missed), items)
    if due:
        nearest = min(n.due_date for n in due)
        return base.Drift(base.DRIFTING, f"{len(due)} decision(s) due",
                          f"The nearest notice deadline is {money.day(nearest)}.", len(due), items)
    if notices:
        nearest = min(n.due_date for n in notices)
        return base.Drift(base.HOLDING, f"Next deadline {money.day(nearest)}",
                          "No renewal decision is due yet.", len(notices), items)
    return base.Drift(base.HOLDING, "No notice deadlines", "No contract with a notice period is open.", 0, [])


def _propose(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[base.Candidate]:
    params: Params = mandate.params
    owner = params.contract_owner_user_id or mandate.spec.owner_user_id
    out: list[base.Candidate] = []
    for notice in _open(snapshot, mandate):
        days_left = (notice.due_date - snapshot.as_of).days
        if days_left > params.days_before:
            continue
        when = (f"The notice deadline is {money.day(notice.due_date)}, in {days_left} days."
                if days_left >= 0 else f"The notice deadline was {money.day(notice.due_date)}: decide now, it may "
                "already have renewed.")
        out.append(base.Candidate(
            kind=v.ACTION_CREATE_TASK, mandate=mandate,
            title=f"Decide: {notice.title}"[:300],
            explanation=(f"{notice.title}{' with ' + notice.counterparty_name if notice.counterparty_name else ''}. "
                         f"{when} Settle this task RENEW, CANCEL or RENEGOTIATE; the decision is written on the "
                         "contract's obligation."),
            key_parts=(notice.promise_id, "renewal", notice.due_date.isoformat()),
            workspace_id=notice.workspace_id, work_item_id=notice.work_item_id, promise_id=notice.promise_id,
            params={"task_kind": v.TASK_DECIDE_RENEWAL, "obligation_id": notice.obligation_id,
                    "severity": "HIGH" if days_left <= 7 else "MEDIUM",
                    **({"assignee_user_id": owner} if owner else {})}))
    return out


def _forecast(snapshot: Snapshot, mandate: base.ActiveMandate, sim) -> list[base.Risk]:
    params: Params = mandate.params
    out: list[base.Risk] = []
    for notice in _open(snapshot, mandate):
        days_left = (notice.due_date - snapshot.as_of).days
        if params.days_before < days_left <= 90:
            out.append(base.Risk(mandate=KEY, on=notice.due_date,
                                 summary=f"{notice.title}: notice deadline {money.day(notice.due_date)}."))
    return out


def _validate(spec: MandateSpec, params: Params) -> list[str]:
    if params.contract_owner_user_id:
        import uuid

        try:
            uuid.UUID(params.contract_owner_user_id)
        except ValueError:
            return ["The contract owner must be a person in this organization."]
    return []



def _backtest(history, mandate: base.ActiveMandate) -> list[base.Finding]:
    out: list[base.Finding] = []
    for notice in history.snapshot.notices:
        if notice.due_date is None or not history.within(notice.due_date) or not base.in_scope(
                mandate, workspace_id=notice.workspace_id, counterparty_key=None, currency=None):
            continue
        if notice.status == v.STATUS_BROKEN or (notice.status == v.STATUS_OPEN and notice.due_date < history.end):
            out.append(base.Finding(
                mandate=KEY, on=notice.due_date, action=v.ACTION_CREATE_TASK, honesty=v.HONESTY_CONFIRMED,
                category=base.CATEGORY_RENEWAL, currency=None, value_micros=0,
                summary=(f"{notice.title}: the notice deadline of {money.day(notice.due_date)} passed with no "
                         "decision recorded. Helm would have asked for one in time."),
                evidence=[{"obligation_id": notice.obligation_id, "work_item_id": notice.work_item_id}]))
    return out


TEMPLATE = base.Template(
    key=KEY, kind=v.MANDATE_KIND_LIMIT, title="Decide every renewal before its notice deadline",
    group=base.GROUP_CONTRACTS, order=9,
    summary="A decision task (renew, cancel or renegotiate) N days before each notice deadline.", Params=Params,
    readback=_readback, evaluate=_evaluate, propose=_propose, forecast=_forecast, validate=_validate, backtest=_backtest,
    keywords=("renewal", "renew", "notice period", "notice deadline", "auto-renew", "contract", "cancel",
              "terminate"),
    recommended_for=("logistics", "construction", "retail", "accounting_firm", "manufacturer"),
)
