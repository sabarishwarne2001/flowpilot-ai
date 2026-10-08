"""ARCH49-S1:conformance — ARCH-37 flows and ARCH-43 case templates, replayed.

FLOWS
=====
Each rule's current graph (`graph_service.load_graph`, so a legacy flat rule is
its flattened graph) becomes a net (`petri.flow_net`). Each finished execution
(COMPLETED / FAILED / TIMED_OUT / BUDGET_EXHAUSTED) is replayed from its node
runs in `sequence` order: SKIPPED runs are branches not taken and are not steps;
a node that ran more than once (a walk paused for review and resumed) counts
once, its last settled run; a branch / condition node names the side it took
(`details.matched`), an assertion node its edge (`details.edge`); a FAILED node
under the rule's HALT policy fires without producing. An execution that started
before the graph was last edited is NOT replayed against the new graph -- it is
counted apart ("graph changed since"), because a deviation from a model the run
never saw is not a deviation.

CASE TEMPLATES
==============
Each template becomes `petri.template_net` over its required slots. Each case
of the template that COMPLETED or CLOSED in the window is replayed from the
EVENT LOG (process_event_objects on CASE): opened, document_added (with the
slot key), inconsistent, completed, closed. Open cases are in progress.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.services.process_intel import mining, petri
from app.services.process_intel import service
from app.services.process_intel import vocabulary as v

FINISHED_EXECUTIONS = ("COMPLETED", "FAILED", "TIMED_OUT", "BUDGET_EXHAUSTED")
SETTLED_RUNS = ("COMPLETED", "FAILED", "TIMED_OUT", "BUDGET_EXHAUSTED")
MAX_EXECUTIONS_PER_RULE = 2000
MAX_CASES_PER_TEMPLATE = 2000


def execution_steps(runs: list[dict[str, Any]], *, halt_on_error: bool) -> Optional[list[petri.ReplayStep]]:
    """Node runs of one execution -> replay steps; None while a run is still pending (in progress)."""
    last: dict[str, dict[str, Any]] = {}
    for run in sorted(runs, key=lambda r: (int(r["sequence"] or 0), str(r["id"]))):
        status = str(run["status"])
        if status in ("PENDING", "RUNNING"):
            return None
        if status == "SKIPPED":
            continue
        last[run["node_key"]] = run
    steps = []
    for run in sorted(last.values(), key=lambda r: (int(r["sequence"] or 0), str(r["id"]))):
        details = run.get("details") or {}
        choice = None
        if run["node_type"] in ("branch", "condition"):
            matched = details.get("matched")
            choice = "true" if matched is True else "false" if matched is False else None
        elif run["node_type"] == "assertion":
            choice = details.get("edge") if details.get("edge") in ("pass", "triage") else None
        failed = str(run["status"]) != "COMPLETED" and halt_on_error
        steps.append(petri.ReplayStep(run["node_key"], choice=choice, failed=failed))
    return steps


def flows(db: Session, *, workspace_id: uuid.UUID, days: int = v.DEFAULT_WINDOW_DAYS,
          at: Optional[datetime] = None) -> list[dict[str, Any]]:
    from app.models.automation import AutomationRule
    from app.services.automation import graph_service

    moment = at or service.now()
    since = moment - timedelta(days=max(1, min(int(days), v.MAX_WINDOW_DAYS)))
    rules = db.execute(select(AutomationRule).where(AutomationRule.workspace_id == workspace_id,
                                                    AutomationRule.deleted_at.is_(None))
                       .order_by(AutomationRule.name)).scalars().all()
    out = []
    for rule in rules:
        try:
            graph = graph_service.load_graph(db, rule=rule)
        except Exception:  # noqa: BLE001 - an unreadable graph is reported, never a 500
            out.append({"rule_id": str(rule.id), "name": rule.name, "error": "graph unreadable"})
            continue
        net = petri.flow_net([(n.node_key, n.node_type) for n in graph.nodes],
                             [(e.from_node_key, e.to_node_key, e.branch) for e in graph.edges])
        edited = db.execute(text(
            "SELECT greatest(r.updated_at, coalesce((SELECT max(updated_at) FROM automation_nodes WHERE rule_id = r.id), "
            "r.updated_at), coalesce((SELECT max(updated_at) FROM automation_edges WHERE rule_id = r.id), r.updated_at)) "
            "FROM automation_rules r WHERE r.id = :r"), {"r": rule.id}).scalar()
        executions = db.execute(text(
            "SELECT id, status::text AS status, started_at FROM automation_executions WHERE rule_id = :r "
            "AND workspace_id = :w AND status::text = ANY(:finished) AND coalesce(started_at, created_at) >= :since "
            "ORDER BY created_at DESC LIMIT :n"),
            {"r": rule.id, "w": workspace_id, "finished": list(FINISHED_EXECUTIONS), "since": since,
             "n": MAX_EXECUTIONS_PER_RULE}).mappings().all()
        runs_by_execution: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        if executions:
            for run in db.execute(text(
                    "SELECT id, execution_id, node_key, node_type, status::text AS status, sequence, details "
                    "FROM automation_node_runs WHERE execution_id = ANY(:ids)"),
                    {"ids": [e["id"] for e in executions]}).mappings().all():
                runs_by_execution[run["execution_id"]].append(dict(run))
        halt = str(getattr(rule, "on_error", "HALT") or "HALT").upper() == "HALT"
        replayed, fitting, changed, in_progress = 0, 0, 0, 0
        fitness_total = 0.0
        missing_at: Counter[str] = Counter()
        remaining_at: Counter[str] = Counter()
        unknown: Counter[str] = Counter()
        worst: list[tuple[float, str]] = []
        for execution in executions:
            if edited is not None and execution["started_at"] is not None and execution["started_at"] < edited:
                changed += 1
                continue
            steps = execution_steps(runs_by_execution.get(execution["id"], []), halt_on_error=halt)
            if steps is None:
                in_progress += 1
                continue
            result = petri.replay(net, steps)
            replayed += 1
            fitness_total += result.fitness
            fitting += int(result.fits)
            missing_at.update(_node_of(p) for p in result.missing_at)
            remaining_at.update({_node_of(p): n for p, n in result.remaining_at.items()})
            unknown.update(result.unknown)
            worst.append((result.fitness, str(execution["id"])))
        worst.sort()
        out.append({
            "rule_id": str(rule.id), "name": rule.name, "is_active": bool(rule.is_active),
            "nodes": len(graph.nodes), "executions": len(executions), "replayed": replayed,
            "fitting": fitting, "graph_changed_since": changed, "in_progress": in_progress,
            "fitness": round(fitness_total / replayed, 6) if replayed else None,
            "missing_at": dict(missing_at.most_common(10)), "remaining_at": dict(remaining_at.most_common(10)),
            "unknown_steps": dict(unknown.most_common(10)),
            "worst": [{"execution_id": e, "fitness": f} for f, e in worst[:5] if f < 1.0],
        })
    return out


def _node_of(place: str) -> str:
    return place.split(":", 1)[1] if place.startswith("in:") else place


def templates(db: Session, *, workspace_id: uuid.UUID, days: int = v.DEFAULT_WINDOW_DAYS,
              at: Optional[datetime] = None) -> list[dict[str, Any]]:
    from app.models.cases import CaseTemplate

    moment = at or service.now()
    since = moment - timedelta(days=max(1, min(int(days), v.MAX_WINDOW_DAYS)))
    rows = db.execute(select(CaseTemplate).where(CaseTemplate.workspace_id == workspace_id,
                                                 CaseTemplate.status.in_(("PUBLISHED", "RETIRED")))
                      .order_by(CaseTemplate.key, CaseTemplate.version)).scalars().all()
    out = []
    for template in rows:
        slots = [(str(s.get("doc_type")), int(s.get("min_count") or 1)) for s in (template.required_documents or [])
                 if s.get("doc_type")]
        net = petri.template_net(slots)
        cases = db.execute(text(
            "SELECT id, status, completed_at, closed_at FROM cases WHERE template_id = :t AND workspace_id = :w "
            "AND created_at >= :since ORDER BY created_at DESC LIMIT :n"),
            {"t": template.id, "w": workspace_id, "since": since, "n": MAX_CASES_PER_TEMPLATE}).mappings().all()
        finished = [c for c in cases if c["status"] in ("COMPLETE", "CLOSED")]
        events = _case_events(db, workspace_id=workspace_id, case_ids=[c["id"] for c in finished])
        replayed, fitting = 0, 0
        fitness_total = 0.0
        missing_at: Counter[str] = Counter()
        remaining_at: Counter[str] = Counter()
        rework = 0
        for case in finished:
            trace = events.get(case["id"], [])
            if not any(a == "case.opened" for a, _ in trace):
                continue  # opened before the log began: nothing to replay from
            steps = petri.case_steps(trace, slots)
            result = petri.replay(net, steps)
            replayed += 1
            fitness_total += result.fitness
            fitting += int(result.fits)
            missing_at.update(result.missing_at)
            remaining_at.update(result.remaining_at)
            rework += sum(1 for s in steps if s.transition == "case.inconsistent")
        out.append({
            "template_id": str(template.id), "key": template.key, "version": int(template.version),
            "name": template.name, "slots": [{"doc_type": t, "min_count": n} for t, n in slots],
            "cases": len(cases), "finished": len(finished), "replayed": replayed, "fitting": fitting,
            "in_progress": len(cases) - len(finished),
            "fitness": round(fitness_total / replayed, 6) if replayed else None,
            "missing_at": dict(missing_at.most_common(10)), "remaining_at": dict(remaining_at.most_common(10)),
            "inconsistent_evaluations": rework,
        })
    return out


def _case_events(db: Session, *, workspace_id: uuid.UUID, case_ids: list[Any]) -> dict[Any, list[tuple[str, Optional[str]]]]:
    if not case_ids:
        return {}
    rows = db.execute(text(
        "SELECT o.object_id, o.activity, e.attributes->>'document_type' AS doc_type, o.occurred_at, e.source_key "
        "FROM process_event_objects o JOIN process_events e ON e.id = o.event_id "
        "WHERE o.workspace_id = :w AND o.object_type = 'CASE' AND o.object_id = ANY(:ids)"),
        {"w": workspace_id, "ids": list(case_ids)}).all()
    ordered = sorted(rows, key=lambda r: (str(r[0]), r[3], mining.tie_order(r[1], r[4])))
    out: dict[Any, list[tuple[str, Optional[str]]]] = defaultdict(list)
    for object_id, activity, doc_type, _at, _key in ordered:
        out[object_id].append((activity, doc_type))
    return out


__all__ = ["execution_steps", "flows", "templates"]
