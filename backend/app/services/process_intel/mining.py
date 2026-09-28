"""ARCH49-S1:mining — directly-follows graphs, variants and their timings. Pure.

A TRACE is one object's events in time order (the case notion is the object
type the log was flattened on). From traces:

  activities    how often each activity occurs, and in how many objects
  edges         the directly-follows relation a -> b (b is the next event of the
                same object after a) with its frequency and the time between
                them (mean, median, 90th percentile, in seconds)
  starts, ends  how often each activity begins / ends a trace
  variants      the distinct activity sequences, most frequent first, with their
                share of objects and throughput times (first event to last)

Ties in time are broken by the event's source key order the loader supplies,
so the same log always mines to the same graph (verify_arch49 M1 checks it
against a hand-computed log, and that a permutation of the input does not
change the result).
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

#: A variant longer than this is keyed on its first MAX_VARIANT_STEPS activities plus the rest's count.
MAX_VARIANT_STEPS = 80


@dataclass(frozen=True)
class Step:
    activity: str
    at: datetime
    order: str = ""          # tie-break within one timestamp (the event's source key)
    actor_kind: str = "SYSTEM"


@dataclass
class Trace:
    object_id: str
    steps: list[Step] = field(default_factory=list)

    def ordered(self) -> list[Step]:
        return sorted(self.steps, key=lambda s: (s.at, s.order, s.activity))

    @property
    def activities(self) -> tuple[str, ...]:
        return tuple(s.activity for s in self.ordered())

    def duration_seconds(self) -> float:
        steps = self.ordered()
        return (steps[-1].at - steps[0].at).total_seconds() if len(steps) > 1 else 0.0


#: Within one timestamp (a transaction writes several rows at the same now()), beginnings sort first and
#: endings last, so a case is opened before its first document is added and a review closes after it is
#: decided -- whatever the rows' ids. A document's upload comes before every other beginning at its instant:
#: nothing can raise a finding on, open a review of, or plan a posting for a document that does not exist yet.
_ORIGIN = ("uploaded",)
_FIRST = ("opened", "planned", "raised", "proposed", "started", "enqueued")
_LAST = ("closed", "completed", "acknowledged", "cancelled", "confirmed", "dismissed", "applied", "auto_applied",
         "rejected", "undone", "superseded", "succeeded", "dead", "resolved")


def lifecycle_rank(activity: str) -> int:
    tail = activity.rsplit(".", 1)[-1]
    if tail in _ORIGIN:
        return 0
    if tail in _FIRST:
        return 1
    if tail in _LAST:
        return 3
    return 2


def tie_order(activity: str, key: str) -> str:
    return f"{lifecycle_rank(activity)}:{key}"


def percentile(values: Sequence[float], q: float) -> float:
    """Linear interpolation between closest ranks (numpy's default), without numpy."""
    if not values:
        return 0.0
    data = sorted(values)
    if len(data) == 1:
        return float(data[0])
    rank = (len(data) - 1) * q
    lo = int(rank)
    hi = min(lo + 1, len(data) - 1)
    return float(data[lo] + (data[hi] - data[lo]) * (rank - lo))


def _stats(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "median": 0.0, "p90": 0.0}
    return {"mean": round(sum(values) / len(values), 3), "median": round(percentile(values, 0.5), 3),
            "p90": round(percentile(values, 0.9), 3)}


def variant_key(activities: Sequence[str]) -> str:
    head = list(activities[:MAX_VARIANT_STEPS])
    if len(activities) > MAX_VARIANT_STEPS:
        head.append(f"+{len(activities) - MAX_VARIANT_STEPS}")
    return hashlib.sha1(">".join(head).encode("utf-8")).hexdigest()[:16]


def dfg(traces: Iterable[Trace], *, max_edges: int = 400) -> dict[str, Any]:
    events: Counter[str] = Counter()
    objects: Counter[str] = Counter()
    edge_count: Counter[tuple[str, str]] = Counter()
    edge_times: dict[tuple[str, str], list[float]] = defaultdict(list)
    starts: Counter[str] = Counter()
    ends: Counter[str] = Counter()
    total = 0
    for trace in traces:
        steps = trace.ordered()
        if not steps:
            continue
        total += 1
        starts[steps[0].activity] += 1
        ends[steps[-1].activity] += 1
        for step in steps:
            events[step.activity] += 1
        for activity in {s.activity for s in steps}:
            objects[activity] += 1
        for a, b in zip(steps, steps[1:]):
            edge_count[(a.activity, b.activity)] += 1
            edge_times[(a.activity, b.activity)].append((b.at - a.at).total_seconds())
    edges = sorted(edge_count.items(), key=lambda kv: (-kv[1], kv[0]))[:max_edges]
    return {
        "objects": total,
        "activities": [{"activity": a, "events": events[a], "objects": objects[a]}
                       for a in sorted(events, key=lambda a: (-events[a], a))],
        "edges": [{"source": a, "target": b, "count": n, "seconds": _stats(edge_times[(a, b)])}
                  for (a, b), n in edges],
        "starts": [{"activity": a, "count": n} for a, n in sorted(starts.items(), key=lambda kv: (-kv[1], kv[0]))],
        "ends": [{"activity": a, "count": n} for a, n in sorted(ends.items(), key=lambda kv: (-kv[1], kv[0]))],
    }


def variants(traces: Iterable[Trace], *, limit: int = 25, examples: int = 5) -> list[dict[str, Any]]:
    groups: dict[str, list[Trace]] = defaultdict(list)
    sequences: dict[str, tuple[str, ...]] = {}
    total = 0
    for trace in traces:
        activities = trace.activities
        if not activities:
            continue
        total += 1
        key = variant_key(activities)
        groups[key].append(trace)
        sequences.setdefault(key, activities)
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), sequences[kv[0]]))[:limit]
    out = []
    for key, members in ranked:
        durations = [m.duration_seconds() for m in members]
        out.append({
            "id": key,
            "activities": list(sequences[key][:MAX_VARIANT_STEPS]),
            "length": len(sequences[key]),
            "count": len(members),
            "share": round(len(members) / total, 6) if total else 0.0,
            "seconds": _stats(durations),
            "object_ids": sorted(m.object_id for m in members)[:examples],
            "members": [m.object_id for m in members],
        })
    return out


def throughput(traces: Iterable[Trace]) -> dict[str, float]:
    return _stats([t.duration_seconds() for t in traces if t.steps])


def rework(trace: Trace) -> int:
    """How many events repeat an activity the object already had (a loop back)."""
    seen: set[str] = set()
    repeats = 0
    for step in trace.ordered():
        if step.activity in seen:
            repeats += 1
        seen.add(step.activity)
    return repeats


__all__ = ["MAX_VARIANT_STEPS", "Step", "Trace", "dfg", "lifecycle_rank", "percentile", "rework", "throughput",
           "tie_order", "variant_key", "variants"]
