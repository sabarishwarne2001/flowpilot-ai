"""What-if: a condition on one document, and the ripple it sends through the mesh. Pure: no I/O.

    "Supplier delivery delayed by 14 days"   DELAY            {days}
    "Clause 8.2 invoked"                     CLAUSE_INVOKED   {clause, clause_type?}
    "Price rises 8%"                         AMOUNT_CHANGE    {percent}
    "The MSA is terminated"                  TERMINATION      {}
    "The supplier defaults"                  PARTY_DEFAULT    {party?}

The traversal is best-first over the document graph: each link carries the change with a factor
(parent to child strongly, child to parent less, peers and shared parties weakly) times the link's
strength, and a document's impact is the strongest path that reaches it. Effects are then read from
each reached document's own facts and terms, in three dimensions:

  FINANCIAL     money that moves, is held, is claimable or loses its authority
  OPERATIONAL   dates that shift, receipts and deliveries that slip, obligations that move
  LEGAL         terms breached, notice windows, performance outside an agreement's term

Nothing is invented: a liquidated-damages figure appears only when a reached agreement states a
rate, and every legal effect carries the sentence it rests on.
"""

from __future__ import annotations

import heapq
import math
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from app.services.truthmesh import vocabulary as v
from app.services.truthmesh.conflicts import money
from app.services.truthmesh.facts import Twin
from app.services.truthmesh.linker import LinkDraft

CLAUSE_TYPES: dict[str, tuple[str, ...]] = {
    "termination": ("terminat", "cancel"),
    "payment": ("payment", "pay ", "invoice", "fees"),
    "liability": ("liabilit", "indemn", "damages"),
    "force_majeure": ("force majeure", "act of god"),
    "price": ("price", "rate", "escalat"),
    "delivery": ("deliver", "shipment", "dispatch"),
    "confidentiality": ("confidential",),
    "insurance": ("insurance",),
    "reporting": ("report",),
}


@dataclass
class Reached:
    twin: Twin
    impact: float
    depth: int
    path: list[uuid.UUID]
    via: Optional[str]
    effects: list[dict[str, Any]] = field(default_factory=list)


def classify_clause(text: str) -> str:
    low = f" {text.lower()} "
    for kind, words in CLAUSE_TYPES.items():
        if any(w in low for w in words):
            return kind
    return "other"


def _edges(links: Sequence[LinkDraft]) -> dict[uuid.UUID, list[tuple[uuid.UUID, float, str, str]]]:
    adjacency: dict[uuid.UUID, list[tuple[uuid.UUID, float, str, str]]] = {}
    for link in links:
        s = float(link.strength)
        if link.relation == v.REL_VERSION_OF:
            down = up = 1.0
        elif link.directed:
            down, up = v.PROPAGATION_DOWN, v.PROPAGATION_UP
        elif link.relation == v.REL_SHARES_PARTY:
            down = up = v.PROPAGATION_PARTY
        else:
            down = up = v.PROPAGATION_PEER
        # source is the child for a directed link: parent -> child is "down".
        adjacency.setdefault(link.target, []).append((link.source, s * down, link.relation, "down"))
        adjacency.setdefault(link.source, []).append((link.target, s * up, link.relation, "up"))
    return adjacency


def traverse(origin: uuid.UUID, links: Sequence[LinkDraft], twins: Mapping[uuid.UUID, Twin]) -> list[Reached]:
    adjacency = _edges(links)
    best: dict[uuid.UUID, Reached] = {origin: Reached(twins[origin], 1.0, 0, [origin], None)}
    queue: list[tuple[float, str, uuid.UUID]] = [(-1.0, str(origin), origin)]
    while queue and len(best) < v.RIPPLE_MAX_NODES:
        negative, _, node = heapq.heappop(queue)
        current = best[node]
        if -negative < current.impact - 1e-12 or current.depth >= v.RIPPLE_MAX_DEPTH:
            continue
        for neighbour, factor, relation, _direction in adjacency.get(node, []):
            twin = twins.get(neighbour)
            if twin is None:
                continue
            impact = round(current.impact * factor, 4)
            if impact < v.RIPPLE_MIN_IMPACT:
                continue
            known = best.get(neighbour)
            if known is None or impact > known.impact + 1e-9:
                best[neighbour] = Reached(twin, impact, current.depth + 1, current.path + [neighbour], relation)
                heapq.heappush(queue, (-impact, str(neighbour), neighbour))
    return sorted(best.values(), key=lambda r: (r.depth != 0, -r.impact, r.depth, r.twin.title))


def _effect(dimension: str, severity: str, title: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"dimension": dimension, "severity": severity, "title": title, "detail": detail,
            **{k: val for k, val in extra.items() if val is not None}}


def _money_key(t: Twin) -> str:
    """One payment per document number: two copies of one invoice are one payment at risk."""
    return f"{t.kind}:{t.identifiers[0]}" if t.identifiers else f"doc:{t.work_item_id}"


def _shift(when: Optional[date], days: int) -> Optional[date]:
    return when + timedelta(days=days) if when else None


# --------------------------------------------------------------------------- scenarios

def _delay(reached: list[Reached], origin: Twin, days: int, obligations: Mapping[uuid.UUID, list[dict]]) -> None:
    agreements = [r.twin for r in reached if r.twin.kind in v.AGREEMENT_KINDS]
    base_amount = origin.net_amount_micros or origin.amount_micros
    for r in reached:
        t = r.twin
        likely = "" if r.impact >= 0.75 else " (likely)"
        if r.depth == 0:
            r.effects.append(_effect(v.DIMENSION_OPERATIONAL, v.SEVERITY_HIGH,
                                     f"{t.title} slips {days} days",
                                     f"The condition starts here: delivery or performance on the "
                                     f"{t.kind_label.lower()} moves {days} days later."))
        if t.kind == "GOODS_RECEIPT" and t.document_date:
            r.effects.append(_effect(v.DIMENSION_OPERATIONAL, v.SEVERITY_MEDIUM, f"Receipt {t.title} moves{likely}",
                                     f"Goods expected {_shift(t.document_date, days).isoformat()} instead of "
                                     f"{t.document_date.isoformat()}.", date_from=t.document_date.isoformat(),
                                     date_to=_shift(t.document_date, days).isoformat()))
        if t.kind in v.SPEND_KINDS and r.depth > 0:
            r.effects.append(_effect(v.DIMENSION_FINANCIAL, v.SEVERITY_MEDIUM,
                                     f"{t.title} is held until the goods arrive{likely}",
                                     f"Three-way matching cannot clear {money(t.amount_micros, t.currency)} until the "
                                     f"receipt exists, so the payment waits about {days} days.",
                                     amount_micros=t.amount_micros, currency=t.currency, exposure=False))
            if t.due_date:
                r.effects.append(_effect(v.DIMENSION_OPERATIONAL, v.SEVERITY_LOW, f"Payment of {t.title} moves",
                                         f"Due {t.due_date.isoformat()}; with the delay, "
                                         f"{_shift(t.due_date, days).isoformat()}.",
                                         date_from=t.due_date.isoformat(), date_to=_shift(t.due_date, days).isoformat()))
        if t.kind in v.AGREEMENT_KINDS:
            delivery = t.terms.get("delivery_days")
            if delivery and days > 0:
                r.effects.append(_effect(v.DIMENSION_LEGAL, v.SEVERITY_HIGH,
                                         f"Breaches the {delivery['value']}-day delivery term of {t.title}",
                                         f"A {days}-day delay exceeds what {t.title} allows.", quote=delivery.get("quote"),
                                         breach=True))
            ld = t.terms.get("liquidated_damages_pct")
            if ld and base_amount:
                unit = str(ld["unit"]).split("/")[-1]
                periods = days if unit == "day" else math.ceil(days / 7) if unit == "week" else math.ceil(days / 30)
                claim = int(Decimal(base_amount) * Decimal(str(ld["value"])) / 100 * periods)
                r.effects.append(_effect(v.DIMENSION_FINANCIAL, v.SEVERITY_MEDIUM,
                                         f"Liquidated damages claimable under {t.title}",
                                         f"{ld['value']}% per {unit} for {periods} {unit}(s) on "
                                         f"{money(base_amount, origin.currency)}: about "
                                         f"{money(claim, origin.currency)} in the buyer's favour.",
                                         amount_micros=claim, currency=origin.currency, quote=ld.get("quote"),
                                         exposure=False))
            deadline = t.terms.get("termination_deadline")
            start = origin.document_date or origin.due_date
            if deadline and start:
                limit = date.fromisoformat(deadline["value"])
                if _shift(start, days) and _shift(start, days) > limit >= start:
                    r.effects.append(_effect(v.DIMENSION_LEGAL, v.SEVERITY_HIGH,
                                             f"The notice window of {t.title} closes first",
                                             f"Notice must be given before {limit.isoformat()}; the delayed date "
                                             f"({_shift(start, days).isoformat()}) is past it.",
                                             quote=deadline.get("quote"), breach=True))
            if t.end_date:
                for other in reached:
                    moved = _shift(other.twin.document_date, days) if other.depth > 0 or other is r else None
                    if other.twin.kind not in v.AGREEMENT_KINDS and moved and other.twin.document_date \
                            and other.twin.document_date <= t.end_date < moved:
                        r.effects.append(_effect(v.DIMENSION_LEGAL, v.SEVERITY_CRITICAL,
                                                 f"{other.twin.title} would fall after {t.title} ends",
                                                 f"Moved to {moved.isoformat()}, after the term ends "
                                                 f"({t.end_date.isoformat()}): no contractual cover.", breach=True))
        for obligation in obligations.get(t.work_item_id, []):
            due = obligation.get("due_date")
            if not due:
                continue
            due_date = date.fromisoformat(due) if isinstance(due, str) else due
            moved = _shift(due_date, days)
            limit = next((a.end_date for a in agreements if a.end_date), None)
            breach = bool(limit and moved and moved > limit >= due_date)
            r.effects.append(_effect(v.DIMENSION_OPERATIONAL, v.SEVERITY_HIGH if breach else v.SEVERITY_LOW,
                                     f"Obligation “{obligation.get('title', 'obligation')}” moves",
                                     f"Due {due_date.isoformat()} becomes {moved.isoformat()}"
                                     + (f", after the agreement ends ({limit.isoformat()})." if breach else "."),
                                     obligation_id=str(obligation.get("id")), breach=breach or None))


def _amount_change(reached: list[Reached], origin: Twin, percent: float, twins: Mapping[uuid.UUID, Twin],
                   links: Sequence[LinkDraft]) -> None:
    factor = Decimal(str(percent)) / 100
    parents: dict[uuid.UUID, list[uuid.UUID]] = {}
    for link in links:
        if link.directed and link.relation in v.DRAWDOWN_RELATIONS:
            parents.setdefault(link.source, []).append(link.target)
    reached_ids = {r.twin.work_item_id for r in reached}
    # When the change is on an authority whose bills are reached, the money that moves is the bills'.
    billed = any(origin.work_item_id in parents.get(r.twin.work_item_id, []) and r.twin.kind in v.SPEND_KINDS
                 for r in reached)
    for r in reached:
        t = r.twin
        base = t.net_amount_micros or t.amount_micros
        if base is None:
            continue
        delta = int(Decimal(base) * factor)
        # A price change flows down to what bills against the changed document, never sideways to a
        # sibling invoice.
        if r.depth == 0 or (origin.work_item_id in parents.get(t.work_item_id, []) and t.kind in v.SPEND_KINDS):
            counts = delta > 0 and (r.depth > 0 or not billed)
            r.effects.append(_effect(v.DIMENSION_FINANCIAL, v.SEVERITY_MEDIUM if abs(percent) < 10 else v.SEVERITY_HIGH,
                                     f"{t.title} {'rises' if delta >= 0 else 'falls'} by {money(abs(delta), t.currency)}",
                                     f"{percent:+g}% on {money(base, t.currency)} is {money(base + delta, t.currency)}.",
                                     amount_micros=abs(delta), currency=t.currency, exposure=counts,
                                     dedupe=_money_key(t)))
            for parent_id in parents.get(t.work_item_id, []):
                parent = twins.get(parent_id)
                if parent is None or parent_id == origin.work_item_id or parent_id not in reached_ids | {parent_id}:
                    continue
                authority = parent.net_amount_micros or parent.amount_micros
                if authority is not None and base + delta > authority >= base:
                    r.effects.append(_effect(v.DIMENSION_LEGAL, v.SEVERITY_HIGH, f"{t.title} would exceed {parent.title}",
                                             f"At {money(base + delta, t.currency)} it is over the "
                                             f"{money(authority, parent.currency)} the {parent.kind_label.lower()} "
                                             f"authorises: a new approval is needed.", breach=True))


def _termination(reached: list[Reached], origin: Twin, clause: Optional[dict[str, Any]],
                 links: Sequence[LinkDraft] = ()) -> None:
    notice = origin.terms.get("notice_days")
    twins = {r.twin.work_item_id: r.twin for r in reached}
    billed: dict[uuid.UUID, dict[str, int]] = {}
    for link in links:
        child = twins.get(link.source)
        if link.directed and link.relation in v.DRAWDOWN_RELATIONS and child is not None and child.kind in v.SPEND_KINDS:
            billed.setdefault(link.target, {})[_money_key(child)] = child.net_amount_micros or child.amount_micros or 0
    for r in reached:
        t = r.twin
        if r.depth == 0:
            detail = (f"{t.title} ends" + (f" after {notice['value']} days' notice" if notice else "") + ".")
            r.effects.append(_effect(v.DIMENSION_LEGAL, v.SEVERITY_CRITICAL, f"{t.title} is terminated", detail,
                                     quote=(clause or {}).get("quote") or (notice or {}).get("quote")))
            continue
        if t.kind in v.AUTHORITY_KINDS or t.kind in v.SPEND_KINDS:
            open_amount = t.net_amount_micros or t.amount_micros
            at_risk = open_amount
            if t.kind in v.AUTHORITY_KINDS and open_amount is not None:
                # An order's money is at risk only where it is not already billed (the bills count).
                at_risk = max(0, open_amount - sum(billed.get(t.work_item_id, {}).values()))
            r.effects.append(_effect(v.DIMENSION_FINANCIAL, v.SEVERITY_HIGH if open_amount else v.SEVERITY_MEDIUM,
                                     f"{t.title} loses its contractual cover",
                                     f"The {t.kind_label.lower()} ({money(open_amount, t.currency)}) depends on "
                                     f"{origin.title}; work or billing under it after termination is unauthorised.",
                                     amount_micros=at_risk, currency=t.currency, exposure=bool(at_risk),
                                     dedupe=_money_key(t)))
        else:
            r.effects.append(_effect(v.DIMENSION_OPERATIONAL, v.SEVERITY_MEDIUM, f"{t.title} must be wound down",
                                     f"It follows from {origin.title}, which is ending."))


def _clause(reached: list[Reached], origin: Twin, clause: dict[str, Any],
            related: Mapping[uuid.UUID, list[dict[str, Any]]], links: Sequence[LinkDraft] = ()) -> None:
    kind = clause.get("clause_type") or classify_clause(clause.get("quote", ""))
    label = clause.get("label") or "The clause"
    if kind == "termination":
        _termination(reached, origin, clause, links)
    for r in reached:
        t = r.twin
        if r.depth == 0 and kind != "termination":
            r.effects.append(_effect(v.DIMENSION_LEGAL, v.SEVERITY_HIGH, f"{label} of {t.title} is invoked",
                                     f"A {kind.replace('_', ' ')} clause: its consequences follow below.",
                                     quote=clause.get("quote")))
        for match in related.get(t.work_item_id, [])[:2]:
            if r.depth == 0:
                continue
            r.effects.append(_effect(v.DIMENSION_LEGAL, v.SEVERITY_MEDIUM, f"Related clause in {t.title}",
                                     f"Reads on the same subject (similarity {match.get('similarity', 0):.0%}).",
                                     quote=match.get("quote")))
        if r.depth == 0:
            continue
        if kind == "payment" and t.kind in v.SPEND_KINDS:
            r.effects.append(_effect(v.DIMENSION_FINANCIAL, v.SEVERITY_MEDIUM, f"Payment of {t.title} is affected",
                                     f"{money(t.amount_micros, t.currency)} is governed by the invoked payment clause.",
                                     amount_micros=t.amount_micros, currency=t.currency, exposure=False))
        if kind in ("liability", "insurance") and t.kind in v.AGREEMENT_KINDS:
            cap = t.terms.get("liability_cap_months")
            r.effects.append(_effect(v.DIMENSION_LEGAL, v.SEVERITY_HIGH, f"Liability under {t.title}",
                                     (f"Liability is capped at {cap['value']} months of fees." if cap else
                                      "No liability cap was found in this document."),
                                     quote=(cap or {}).get("quote")))
        if kind == "force_majeure":
            r.effects.append(_effect(v.DIMENSION_OPERATIONAL, v.SEVERITY_MEDIUM, f"{t.title} may be suspended",
                                     "Performance obligations that depend on the invoked clause are suspended."))
        if kind in ("price",) and t.kind in v.SPEND_KINDS:
            r.effects.append(_effect(v.DIMENSION_FINANCIAL, v.SEVERITY_MEDIUM, f"Pricing of {t.title} may change",
                                     f"{money(t.amount_micros, t.currency)} was priced under the invoked clause.",
                                     amount_micros=t.amount_micros, currency=t.currency, exposure=False))


def _party_default(reached: list[Reached], origin: Twin, party: Optional[str]) -> None:
    who = party or origin.counterparty or "the counterparty"
    for r in reached:
        t = r.twin
        if t.kind in v.SPEND_KINDS:
            r.effects.append(_effect(v.DIMENSION_FINANCIAL, v.SEVERITY_HIGH, f"{t.title} may never be settled",
                                     f"{money(t.amount_micros, t.currency)} with {who}: hold payment until the "
                                     f"position is clear, and check what was prepaid.",
                                     amount_micros=t.amount_micros, currency=t.currency, exposure=True,
                                     dedupe=_money_key(t)))
        elif t.kind == "PURCHASE_ORDER":
            r.effects.append(_effect(v.DIMENSION_OPERATIONAL, v.SEVERITY_HIGH, f"Supply under {t.title} is at risk",
                                     f"Goods ordered from {who} may not arrive; find an alternative supplier."))
        elif t.kind in v.AGREEMENT_KINDS:
            r.effects.append(_effect(v.DIMENSION_LEGAL, v.SEVERITY_HIGH, f"Rights under {t.title}",
                                     f"Insolvency or default of {who} usually allows termination; check the clause.",
                                     quote=(t.terms.get("notice_days") or {}).get("quote")))


# --------------------------------------------------------------------------- entry point

def simulate(*, origin: uuid.UUID, scenario: str, parameters: Mapping[str, Any], twins: Mapping[uuid.UUID, Twin],
             links: Sequence[LinkDraft], obligations: Optional[Mapping[uuid.UUID, list[dict]]] = None,
             clause: Optional[dict[str, Any]] = None,
             related: Optional[Mapping[uuid.UUID, list[dict[str, Any]]]] = None) -> dict[str, Any]:
    if scenario not in v.SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}")
    if origin not in twins:
        raise ValueError("the origin document is not in the mesh")
    start = twins[origin]
    reached = traverse(origin, links, twins)
    if scenario == v.SCENARIO_DELAY:
        _delay(reached, start, int(parameters.get("days", 14)), obligations or {})
    elif scenario == v.SCENARIO_AMOUNT_CHANGE:
        _amount_change(reached, start, float(parameters.get("percent", 10)), twins, links)
    elif scenario == v.SCENARIO_TERMINATION:
        _termination(reached, start, clause, links)
    elif scenario == v.SCENARIO_CLAUSE_INVOKED:
        _clause(reached, start, clause or {"quote": str(parameters.get("clause", "")), "label": "The clause"},
                related or {}, links)
    elif scenario == v.SCENARIO_PARTY_DEFAULT:
        _party_default(reached, start, parameters.get("party"))

    currency = start.currency or next((r.twin.currency for r in reached if r.twin.currency), None)
    at_risk: dict[str, int] = {}
    for r in reached:
        for e in r.effects:
            if e["dimension"] == v.DIMENSION_FINANCIAL and e.get("exposure") and e.get("currency") in (None, currency):
                key = e.get("dedupe") or f"{r.twin.work_item_id}:{e['title']}"
                at_risk[key] = max(at_risk.get(key, 0), int(e.get("amount_micros") or 0))
    exposure = sum(at_risk.values())
    counts = {d: sum(1 for r in reached for e in r.effects if e["dimension"] == d)
              for d in (v.DIMENSION_FINANCIAL, v.DIMENSION_OPERATIONAL, v.DIMENSION_LEGAL)}
    nodes = [{
        "work_item_id": str(r.twin.work_item_id), "title": r.twin.title, "filename": r.twin.filename,
        "kind": r.twin.kind, "kind_label": r.twin.kind_label, "depth": r.depth, "impact": r.impact,
        "via": r.via, "path": [str(p) for p in r.path], "effects": r.effects,
    } for r in reached]
    on_path = {(r.path[i], r.path[i + 1]) for r in reached for i in range(len(r.path) - 1)}
    edges = [{"source": str(link.source), "target": str(link.target), "relation": link.relation,
              "strength": float(link.strength)}
             for link in links if (link.source, link.target) in on_path or (link.target, link.source) in on_path]
    return {
        "origin": str(origin),
        "scenario": scenario,
        "scenario_label": v.SCENARIOS[scenario],
        "parameters": dict(parameters),
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "documents_affected": max(0, len(reached) - 1),
            "max_depth": max((r.depth for r in reached), default=0),
            "financial_exposure_micros": exposure,
            "currency": currency,
            "breaches": sum(1 for r in reached for e in r.effects if e.get("breach")),
            "effects": counts,
            "legal_quotes": sum(1 for r in reached for e in r.effects if e["dimension"] == v.DIMENSION_LEGAL
                                and e.get("quote")),
        },
        "engine_version": v.ENGINE_VERSION,
    }


__all__ = ["CLAUSE_TYPES", "Reached", "classify_clause", "simulate", "traverse"]
