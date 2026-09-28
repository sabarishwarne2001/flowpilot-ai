"""ARCH49-S1:discovery — the event log flattened on one object type, mined and costed.

`discover` reads the objects of one type that had an event in the window
(newest first, at most MAX_OBJECTS), all of each one's events (a trace begun
before the window is whole), mines the directly-follows graph and the variants
(`mining`), and prices every variant with its objects' cost-to-serve (`cost`).
`object_types` is the object-centric overview: per type, how many objects and
events, and which other types its events also touch.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.process_intel import cost as C
from app.services.process_intel import mining
from app.services.process_intel import service
from app.services.process_intel import vocabulary as v


def load_traces(db: Session, *, workspace_id: uuid.UUID, object_type: str, since: datetime,
                until: Optional[datetime] = None, max_objects: int = v.MAX_OBJECTS,
                max_events: int = v.MAX_EVENTS) -> tuple[list[mining.Trace], bool]:
    """(traces, truncated). Ties within a timestamp are ordered by `mining.tie_order`."""
    if object_type not in v.OBJECT_TYPES:
        raise ValueError(f"{object_type!r} is not an object type")
    rows = db.execute(text(
        "WITH chosen AS (SELECT object_id FROM process_event_objects WHERE workspace_id = :w AND object_type = :t "
        "AND occurred_at >= :since AND occurred_at <= :until GROUP BY object_id ORDER BY max(occurred_at) DESC "
        "LIMIT :n) "
        "SELECT o.object_id, o.activity, o.occurred_at, e.source_key, e.actor_kind "
        "FROM process_event_objects o JOIN chosen c ON c.object_id = o.object_id "
        "JOIN process_events e ON e.id = o.event_id "
        "WHERE o.workspace_id = :w AND o.object_type = :t LIMIT :m"),
        {"w": workspace_id, "t": object_type, "since": since, "until": until or service.now(), "n": max_objects,
         "m": max_events + 1}).all()
    truncated = len(rows) > max_events
    traces: dict[Any, mining.Trace] = {}
    for object_id, activity, at, key, actor_kind in rows[:max_events]:
        trace = traces.setdefault(object_id, mining.Trace(object_id=str(object_id)))
        trace.steps.append(mining.Step(activity=activity, at=at, order=mining.tie_order(activity, key),
                                       actor_kind=actor_kind))
    return list(traces.values()), truncated


def discover(db: Session, *, workspace_id: uuid.UUID, object_type: str, days: int = v.DEFAULT_WINDOW_DAYS,
             at: Optional[datetime] = None, with_cost: bool = True) -> dict[str, Any]:
    moment = at or service.now()
    window = max(1, min(int(days), v.MAX_WINDOW_DAYS))
    traces, truncated = load_traces(db, workspace_id=workspace_id, object_type=object_type,
                                    since=moment - timedelta(days=window), until=moment)
    graph = mining.dfg(traces, max_edges=v.MAX_EDGES)
    ranked = mining.variants(traces, limit=v.MAX_VARIANTS)
    costs: dict[uuid.UUID, C.Cost] = {}
    if with_cost and traces:
        costs = C.object_costs(db, workspace_id=workspace_id, object_type=object_type,
                               object_ids=[uuid.UUID(t.object_id) for t in traces])
    touches = {t.object_id: sum(1 for s in t.steps if s.actor_kind == v.ACTOR_PERSON) for t in traces}
    agent = {t.object_id: sum(1 for s in t.steps if s.actor_kind == v.ACTOR_AGENT) for t in traces}
    out_variants = []
    for variant in ranked:
        members = variant.pop("members")
        member_costs = [costs[uuid.UUID(m)] for m in members if uuid.UUID(m) in costs]
        variant["cost"] = C.summarise(member_costs, len(members))
        variant["touches"] = round(sum(touches[m] for m in members) / len(members), 3) if members else 0.0
        variant["agent_events"] = round(sum(agent[m] for m in members) / len(members), 3) if members else 0.0
        out_variants.append(variant)
    return {
        "object_type": object_type,
        "window_days": window,
        "truncated": truncated,
        "throughput_seconds": mining.throughput(traces),
        "graph": graph,
        "variants": out_variants,
        "variant_count": len({mining.variant_key(t.activities) for t in traces}),
        "cost": C.summarise(costs.values(), len(traces)) if with_cost else None,
        "touches_per_object": round(sum(touches.values()) / len(traces), 3) if traces else 0.0,
    }


def object_types(db: Session, *, workspace_id: uuid.UUID, days: int = v.DEFAULT_WINDOW_DAYS,
                 at: Optional[datetime] = None) -> list[dict[str, Any]]:
    moment = at or service.now()
    since = moment - timedelta(days=max(1, min(int(days), v.MAX_WINDOW_DAYS)))
    counts = db.execute(text(
        "SELECT object_type, count(DISTINCT object_id), count(*) FROM process_event_objects "
        "WHERE workspace_id = :w AND occurred_at >= :since GROUP BY object_type"), {"w": workspace_id,
                                                                               "since": since}).all()
    links = db.execute(text(
        "SELECT a.object_type, b.object_type, count(*) FROM process_event_objects a JOIN process_event_objects b "
        "ON b.event_id = a.event_id AND b.object_type <> a.object_type "
        "WHERE a.workspace_id = :w AND a.occurred_at >= :since GROUP BY 1, 2"), {"w": workspace_id,
                                                                            "since": since}).all()
    related: dict[str, Counter[str]] = defaultdict(Counter)
    for a, b, n in links:
        related[a][b] += int(n)
    by_type = {t: (int(o), int(e)) for t, o, e in counts}
    return [{"object_type": t, "label": v.OBJECT_LABELS[t], "objects": by_type.get(t, (0, 0))[0],
             "events": by_type.get(t, (0, 0))[1], "shares_events_with": dict(related[t].most_common())}
            for t in v.OBJECT_TYPES]


def timeline(db: Session, *, workspace_id: uuid.UUID, object_type: str, object_id: uuid.UUID,
             limit: int = 500) -> list[dict[str, Any]]:
    """One object's events, oldest first, with the other objects each event touched (ids only)."""
    rows = db.execute(text(
        "SELECT e.id, e.activity, e.occurred_at, e.source, e.source_key, e.actor_kind, e.actor_user_id, e.attributes "
        "FROM process_event_objects o JOIN process_events e ON e.id = o.event_id "
        "WHERE o.workspace_id = :w AND o.object_type = :t AND o.object_id = :id LIMIT :n"),
        {"w": workspace_id, "t": object_type, "id": object_id, "n": limit}).mappings().all()
    ordered = sorted(rows, key=lambda r: (r["occurred_at"], mining.tie_order(r["activity"], r["source_key"])))
    others: dict[Any, list[dict[str, str]]] = defaultdict(list)
    if ordered:
        for event_id, ot, oid, q in db.execute(text(
                "SELECT event_id, object_type, object_id, qualifier FROM process_event_objects "
                "WHERE event_id = ANY(:ids) AND NOT (object_type = :t AND object_id = :id)"),
                {"ids": [r["id"] for r in ordered], "t": object_type, "id": object_id}).all():
            others[event_id].append({"object_type": ot, "object_id": str(oid), "qualifier": q})
    return [{"id": str(r["id"]), "activity": r["activity"], "occurred_at": r["occurred_at"], "source": r["source"],
             "actor_kind": r["actor_kind"], "actor_user_id": str(r["actor_user_id"]) if r["actor_user_id"] else None,
             "attributes": dict(r["attributes"] or {}), "objects": others.get(r["id"], [])} for r in ordered]


__all__ = ["discover", "load_traces", "object_types", "timeline"]
