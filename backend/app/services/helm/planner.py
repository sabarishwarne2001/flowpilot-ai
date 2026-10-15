"""The Planner: from a snapshot and the active mandates to a guarded plan (pure part).

    compose(snapshot, mandates) -> Composition

Steps (brief §6): evaluate each mandate's drift → collect candidate actions → optimise payment
timing → simulate → re-evaluate the drift against the plan → run the Guard on every action. The
database side (`plan_store.run`) takes the snapshot, persists the result as a new plan version and
notifies; this module never touches a session, so a test can replay any plan exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional, Sequence

from app.services.helm import money, optimizer, simulation
from app.services.helm import vocabulary as v
from app.services.helm.guard import SYSTEM, ActionView, GuardContext, GuardResult, guard
from app.services.helm.mandates import base
from app.services.helm.mandates.registry import TEMPLATES
from app.services.helm.snapshot import Snapshot

#: Templates whose presence means Helm plans payment timing at all.
TIMING_TEMPLATES = ("pay_on_time", "capture_early_discounts", "keep_cash_above", "msme_pay_within_limit")
DEFAULT_TIME_LIMIT_S = 5.0
#: Why a hold can end, by its reason code.
RELEASE_WHY = {
    "PROBABLE_DUPLICATE": "A reviewer cleared the duplicate finding.",
    "PRICE_ABOVE_AGREED": "No line is above the agreed price any more (a corrected invoice or a credit note).",
    "BANK_ACCOUNT_CHANGED": "The new bank account was verified.",
    "NO_MATCHED_PO": "It now has a matching purchase order (or is no longer above the amount).",
}


@dataclass
class Proposal:
    candidate: base.Candidate
    view: ActionView
    result: GuardResult


@dataclass
class Composition:
    snapshot: Snapshot
    mandates: list[base.ActiveMandate]
    drifts: dict[str, base.Drift]
    proposals: list[Proposal]
    solution: Optional[optimizer.Solution]
    sim: Optional[simulation.SimulationResult]
    forecast_sim: Optional[simulation.SimulationResult]
    seed: int
    status: str
    conflict: Optional[dict[str, Any]] = None
    risks: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        schedule = self.solution.schedule if self.solution else {}
        payments = [p for p in self.proposals if p.candidate.kind == v.ACTION_SCHEDULE_PAYMENT]
        by_currency: dict[str, int] = {}
        for p in payments:
            by_currency[p.candidate.currency or "?"] = by_currency.get(p.candidate.currency or "?", 0) + int(
                p.candidate.amount_micros or 0)
        return {
            "instruments": [
                {"mandate_id": m.id, "version": m.version, "template": m.template, "title": m.title,
                 "kind": m.spec.kind, "mode": m.mode, **self.drifts[m.id].as_json()}
                for m in self.mandates if m.id in self.drifts
            ],
            "counts": {"actions": len(self.proposals), "payments": len(payments),
                       "needs_approval": sum(1 for p in self.proposals if p.result.needs_approval and p.result.passed),
                       "blocked": sum(1 for p in self.proposals if not p.result.passed)},
            "payments_by_currency": by_currency,
            "optimizer": {
                "status": self.solution.status if self.solution else v.SOLVER_EMPTY,
                "solver": self.solution.solver if self.solution else "none",
                "discounts_micros": self.solution.discounts_micros if self.solution else 0,
                "late_penalties_micros": self.solution.late_penalties_micros if self.solution else 0,
                "early_cost_micros": self.solution.early_cost_micros if self.solution else 0,
                "deferred": sorted(k for k, d in schedule.items() if d is None),
                "duration_ms": self.solution.duration_ms if self.solution else 0,
            },
            "simulation": self.sim.as_json() if self.sim else None,
            "forecast": self.forecast_sim.as_json() if self.forecast_sim else None,
            "cash": {c: {"amount_micros": x.amount_micros, "as_of": x.as_of.isoformat(), "source": x.source,
                         "age_days": x.age_days, "evidence": x.evidence} for c, x in self.snapshot.cash.items()},
            "visibility": self.snapshot.visibility,
            "risks": self.risks,
            "run_days": [d.isoformat() for d in self.snapshot.run_days],
            "as_of": self.snapshot.as_of.isoformat(),
        }


def _merge(terms: Sequence[base.OptimizerTerms]) -> base.OptimizerTerms:
    merged = base.OptimizerTerms()
    for t in terms:
        for key, why in t.exclude.items():
            merged.exclude.setdefault(key, why)
        for key, (deadline, kind, why) in t.deadlines.items():
            current = merged.deadlines.get(key)
            # The earlier deadline wins; at the same date, the law (MSME) outranks the company's rule.
            if current is None or deadline < current[0] or (deadline == current[0] and kind == "MSME"):
                merged.deadlines[key] = (deadline, kind, why)
        for key, value in t.discounts.items():
            merged.discounts[key] = max(value, merged.discounts.get(key, 0))
        for key, (per_day, once) in t.late_penalties.items():
            old = merged.late_penalties.get(key, (0, 0))
            merged.late_penalties[key] = (max(per_day, old[0]), max(once, old[1]))
        for currency, floor in t.floors.items():
            merged.floors[currency] = max(floor, merged.floors.get(currency, 0))
    return merged


def _plan_destination(candidate: base.Candidate, snapshot: Snapshot) -> tuple[Optional[bool], str]:
    """What the plan can know of an email's destination: whether there is a contact to send to. The
    mail server and egress are checked again, from the database, when the action runs."""
    if candidate.kind not in (v.ACTION_SEND_REMINDER, v.ACTION_REQUEST_DOCUMENT, *v.DRAFT_ACTIONS,
                              v.ACTION_WEBHOOK):
        return None, ""
    if candidate.kind == v.ACTION_WEBHOOK:
        return None, "The webhook destination is checked when the action runs."
    if candidate.kind in v.DRAFT_ACTIONS and (candidate.mandate is None
                                                      or candidate.mandate.mode != v.AUTONOMY_ACT):
        return True, "A draft only: nothing is sent."
    counterparty = snapshot.counterparties.get(candidate.counterparty_key or "")
    contact = candidate.params.get("to") or (counterparty.contact_email if counterparty else None)
    if not contact:
        name = (counterparty.name if counterparty else None) or "this counterparty"
        return False, f"No email contact for {name}; add one in Helm settings."
    return True, f"Email to {contact}; the mail server and egress are checked again when it runs."


def _owner(mandates: Sequence[base.ActiveMandate], template_key: str) -> Optional[base.ActiveMandate]:
    return next((m for m in mandates if m.template == template_key), None)


def compose(snapshot: Snapshot, mandates: Sequence[base.ActiveMandate], *, issued_at: Optional[datetime] = None,
            capability_held: bool = True, time_limit_s: float = DEFAULT_TIME_LIMIT_S,
            forecast_days: int = 90) -> Composition:
    active = [m for m in mandates if m.status == v.MANDATE_ACTIVE and m.template in TEMPLATES]
    seed = simulation.seed_for(snapshot.digest)
    proposals: list[Proposal] = []
    candidates: list[base.Candidate] = []
    for mandate in active:
        if mandate.mode == v.AUTONOMY_WATCH:
            continue
        template = TEMPLATES[mandate.template]
        candidates.extend(template.propose(snapshot, mandate))
        # A hold this mandate placed whose reason is gone: propose releasing it.
        if template.hold_code and template.still_holds is not None:
            for payable in base.payables_in_scope(snapshot, mandate):
                if payable.held_by_helm_for(template.hold_code) and not template.still_holds(snapshot, payable,
                                                                                             mandate):
                    candidates.append(base.release_candidate(payable, mandate, RELEASE_WHY.get(
                        template.hold_code, "Its reason is gone.")))

    timing = [m for m in active if m.template in TIMING_TEMPLATES]
    terms = _merge([TEMPLATES[m.template].optimizer(snapshot, m) for m in active])
    pre = simulation.simulate(snapshot, seed=seed, horizon_days=snapshot.horizon_days)
    solution: Optional[optimizer.Solution] = None
    items: list[optimizer.Item] = []
    if timing:
        for payable in snapshot.payables:
            if not payable.schedulable or payable.promise_id in terms.exclude or not payable.currency:
                continue
            if payable.due_date is None:
                continue
            deadline = terms.deadlines.get(payable.promise_id)
            per_day, once = terms.late_penalties.get(payable.promise_id, (0, 0))
            if payable.terms.late_fee_pct_per_month:
                stated = money.pct_of(payable.amount_micros, payable.terms.late_fee_pct_per_month) // 30
                per_day = max(per_day, stated)
            items.append(optimizer.Item(
                key=payable.promise_id, currency=payable.currency, amount=payable.amount_micros, due=payable.due_date,
                hard_deadline=deadline[0] if deadline else None, deadline_kind=deadline[1] if deadline else None,
                discount_deadline=payable.discount_deadline if payable.promise_id in terms.discounts else None,
                discount_value=terms.discounts.get(payable.promise_id, 0), late_penalty_per_day=per_day,
                late_penalty_fixed=once,
                early_cost_per_day=money.interest(payable.amount_micros, snapshot.cost_of_capital_bps, 1),
                label=f"{payable.document_number or payable.document} ({payable.counterparty_name or 'supplier'})"))
        run_days = list(snapshot.run_days)
        floors = {c: f for c, f in terms.floors.items() if c in snapshot.cash}
        cash = {c: x.amount_micros for c, x in snapshot.cash.items()}
        for c in cash:
            floors.setdefault(c, 0)  # with a known balance, never plan an overdraft
        inflows = {c: [pre.p10_inflow_by(c, d) for d in run_days] for c in cash}
        problem = optimizer.Problem(today=snapshot.as_of, run_days=run_days, horizon_end=snapshot.horizon_end,
                                    items=items, cash=cash, inflows=inflows, floors=floors, time_limit_s=time_limit_s)
        solution = optimizer.solve(problem)

    schedule = solution.schedule if solution else {}
    payable_by_id = {p.promise_id: p for p in snapshot.payables}
    for item in items:
        day = schedule.get(item.key)
        payable = payable_by_id[item.key]
        if day is None:
            continue
        if payable.settlement_state in (v.SETTLEMENT_SCHEDULED, v.SETTLEMENT_APPROVED) and payable.run_date == day:
            continue  # already on that run
        deadline = terms.deadlines.get(item.key)
        discount = item.discount_value if (item.discount_value and item.discount_deadline
                                           and day <= item.discount_deadline) else 0
        if deadline and deadline[1] == "MSME":
            owner = _owner(active, "msme_pay_within_limit")
        elif discount:
            owner = _owner(active, "capture_early_discounts")
        else:
            owner = _owner(active, "pay_on_time") or (timing[0] if timing else None)
        if owner is not None and owner.mode == v.AUTONOMY_WATCH:
            continue
        why = [f"Due {money.day(payable.due_date)}" + (f" ({payable.terms.rule})" if payable.terms.parsed else "")]
        if deadline:
            why.append(f"deadline {money.day(deadline[0])}: {deadline[2]}")
        if discount:
            why.append(f"pays inside the discount window, saving {money.say(discount, payable.currency)}")
        if day > (payable.due_date or day):
            why.append("paid after the due date to keep cash above the floor")
        candidates.append(base.Candidate(
            kind=v.ACTION_SCHEDULE_PAYMENT, mandate=owner,
            title=f"Pay {payable.counterparty_name or 'supplier'} {money.say(payable.amount_micros, payable.currency)}"
                  f" on {day.strftime('%a %d %b')}",
            explanation="; ".join(why) + ".",
            key_parts=(payable.promise_id, day.isoformat(), payable.amount_micros, payable.settlement_version),
            workspace_id=payable.workspace_id, work_item_id=payable.work_item_id, promise_id=payable.promise_id,
            counterparty_key=payable.counterparty_key, amount_micros=payable.amount_micros, currency=payable.currency,
            run_date=day, value_micros=discount or None,
            params={"bank_account": payable.bank_account, "document_number": payable.document_number,
                    "due_date": payable.due_date.isoformat() if payable.due_date else None,
                    "discount_micros": discount}))

    planned: dict[str, list[tuple[date, int]]] = {}
    for candidate in candidates:
        if candidate.kind == v.ACTION_SCHEDULE_PAYMENT and candidate.currency and candidate.run_date:
            planned.setdefault(candidate.currency, []).append((candidate.run_date, int(candidate.amount_micros or 0)))
    for payable in snapshot.payables:
        if payable.settlement_state in (v.SETTLEMENT_SCHEDULED, v.SETTLEMENT_APPROVED) and payable.run_date and \
                payable.currency and payable.promise_id not in {c.promise_id for c in candidates
                                                                if c.kind == v.ACTION_SCHEDULE_PAYMENT}:
            planned.setdefault(payable.currency, []).append((payable.run_date, payable.amount_micros))
    floors_all = {m.params.floor.currency: m.params.floor.amount_micros for m in active
                  if m.template == "keep_cash_above"}
    sim = simulation.simulate(snapshot, seed=seed, horizon_days=snapshot.horizon_days, planned=planned,
                              floors=floors_all)
    forecast = simulation.simulate(snapshot, seed=seed, horizon_days=forecast_days, planned=planned,
                                   floors=floors_all)

    context = {"sim": sim.as_json(), "schedule": schedule, "cash_paths": sim.cash_paths}
    drifts = {m.id: TEMPLATES[m.template].evaluate(snapshot, m, context) for m in active}

    # Guard every candidate, payments in run-day order so each sees the payments ahead of it.
    paid_ahead: dict[str, int] = {}
    ordered = sorted(candidates, key=lambda c: (c.kind != v.ACTION_SCHEDULE_PAYMENT, c.run_date or date.max,
                                                c.idempotency_key))
    for candidate in ordered:
        view = ActionView.of(candidate)
        allowed, detail = _plan_destination(candidate, snapshot)
        ctx = GuardContext(
            stage=v.CERT_STAGE_PLAN, capability_held=capability_held,
            destination_allowed=allowed, destination_detail=detail,
            scheduled_before=dict(paid_ahead),
            inflow_by={c: sim.p10_inflow_by(c, candidate.run_date or snapshot.as_of) for c in snapshot.cash},
            sim=context["sim"], schedule=schedule)
        result = guard(view, snapshot, active, SYSTEM, ctx)
        proposals.append(Proposal(candidate=candidate, view=view, result=result))
        if candidate.kind == v.ACTION_SCHEDULE_PAYMENT and candidate.currency:
            paid_ahead[candidate.currency] = paid_ahead.get(candidate.currency, 0) + int(candidate.amount_micros or 0)

    risks: list[dict[str, Any]] = []
    for mandate in active:
        for risk in TEMPLATES[mandate.template].forecast(snapshot, mandate, forecast):
            risks.append(risk.as_json())

    status = solution.status if solution else v.SOLVER_EMPTY
    conflict = solution.conflict.as_json() if solution and solution.conflict else None
    return Composition(snapshot=snapshot, mandates=active, drifts=drifts, proposals=proposals, solution=solution,
                       sim=sim, forecast_sim=forecast, seed=seed, status=status, conflict=conflict, risks=risks)


__all__ = ["Composition", "Proposal", "TIMING_TEMPLATES", "compose"]
