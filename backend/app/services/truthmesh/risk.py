"""Risk scores: per document, and the workspace's index. Pure: no I/O.

A document's risk is the noisy-OR of the open conflicts that name it, each pulling by its severity
(CRITICAL 0.60, HIGH 0.40, MEDIUM 0.20, LOW 0.08): one critical conflict is 60, two are 84, a low one
alone is 8. The workspace index is the mean of its documents' risk weighted by their size (larger
amounts weigh more, logarithmically, so one large contract does not drown every invoice).
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from typing import Iterable, Mapping, Optional

from app.services.truthmesh import vocabulary as v

OPEN_STATUSES = ("OPEN", "ACKNOWLEDGED")


def node_risks(conflicts: Iterable[object]) -> dict[uuid.UUID, float]:
    """work item -> risk in [0, 100], from conflicts that are still open."""
    remaining: dict[uuid.UUID, float] = defaultdict(lambda: 1.0)
    for conflict in conflicts:
        if getattr(conflict, "status", "OPEN") not in OPEN_STATUSES:
            continue
        pull = v.SEVERITY_RISK.get(getattr(conflict, "severity", v.SEVERITY_LOW), 0.0)
        for work_item_id in getattr(conflict, "work_item_ids", []) or []:
            remaining[work_item_id] *= 1.0 - pull
    return {key: round(100.0 * (1.0 - value), 2) for key, value in remaining.items()}


def _weight(amount_micros: Optional[int]) -> float:
    major = (amount_micros or 0) / 1_000_000
    return 1.0 + math.log10(1.0 + max(0.0, major))


def risk_index(risks: Mapping[uuid.UUID, float], amounts: Mapping[uuid.UUID, Optional[int]]) -> float:
    """Size-weighted mean risk over every document in the mesh (documents without conflicts count 0)."""
    if not amounts:
        return 0.0
    total = sum(_weight(amount) for amount in amounts.values())
    weighted = sum(risks.get(key, 0.0) * _weight(amount) for key, amount in amounts.items())
    return round(min(100.0, weighted / total), 2) if total else 0.0


def band(score: float) -> str:
    if score >= 60:
        return "CRITICAL"
    if score >= 35:
        return "HIGH"
    if score >= 15:
        return "ELEVATED"
    return "LOW"


__all__ = ["band", "node_risks", "risk_index"]
