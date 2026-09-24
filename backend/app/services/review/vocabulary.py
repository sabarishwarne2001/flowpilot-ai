"""ARCH-40 — the review hub's vocabulary, in one module with no imports.

Pure by design: `verify_arch40.py` loads it without a database, a session or
an app settings object, so the gates that compare this vocabulary against the
SQL view and the CHECK constraints can run offline.
"""

from __future__ import annotations

from typing import Final

from app.models.review import (
    REVIEW_KIND_ANOMALY,
    REVIEW_KIND_ASSERTION,
    REVIEW_KIND_EXTRACTION,
    REVIEW_KIND_MERGE,
    REVIEW_KIND_SPLIT,
    REVIEW_KINDS,
)

KIND_EXTRACTION: Final[str] = REVIEW_KIND_EXTRACTION
KIND_ASSERTION: Final[str] = REVIEW_KIND_ASSERTION
KIND_ANOMALY: Final[str] = REVIEW_KIND_ANOMALY
#: ARCH42-S1:review-kind-merge
KIND_MERGE: Final[str] = REVIEW_KIND_MERGE
#: ARCH43-S1:review-kind-split
KIND_SPLIT: Final[str] = REVIEW_KIND_SPLIT

KINDS: Final[tuple[str, ...]] = REVIEW_KINDS

SEVERITY_CRITICAL: Final[str] = "CRITICAL"
SEVERITY_HIGH: Final[str] = "HIGH"
SEVERITY_MEDIUM: Final[str] = "MEDIUM"
SEVERITY_LOW: Final[str] = "LOW"

SEVERITIES: Final[tuple[str, ...]] = (
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    SEVERITY_LOW,
)

#: Lower is more urgent, so `ORDER BY severity_rank, created_at` puts the
#: oldest high-severity item first. The view computes the same numbers; gate
#: A6 asserts the two tables agree.
SEVERITY_RANK: Final[dict[str, int]] = {
    SEVERITY_CRITICAL: 1,
    SEVERITY_HIGH: 2,
    SEVERITY_MEDIUM: 3,
    SEVERITY_LOW: 4,
}

STATUS_OPEN: Final[str] = "OPEN"
STATUS_RESOLVED: Final[str] = "RESOLVED"

STATUSES: Final[tuple[str, ...]] = (STATUS_OPEN, STATUS_RESOLVED)

#: ARCH40-S1:review-reasons. Why an item needs a human. The view computes it
#: (arch40_step2a_review_view_paths); gate A6 asserts the two lists agree.
REASON_DISAGREEMENT: Final[str] = "DISAGREEMENT"
REASON_ESCALATION: Final[str] = "ESCALATION"
REASON_CALIBRATION_HOLD: Final[str] = "CALIBRATION_HOLD"
REASON_AUTONOMY_AUDIT: Final[str] = "AUTONOMY_AUDIT"
REASON_PENDING_REVIEW: Final[str] = "PENDING_REVIEW"
REASON_CLAUSE_TRIAGE: Final[str] = "CLAUSE_TRIAGE"
REASON_ANOMALY: Final[str] = "ANOMALY"
#: ARCH42-S1:review-reason-merge. arch42_step1_entity_graph's view produces it.
REASON_ENTITY_MERGE: Final[str] = "ENTITY_MERGE"
#: ARCH43-S1:review-reason-split. arch43_step1_case_intelligence's view produces it.
REASON_PACKET_SPLIT: Final[str] = "PACKET_SPLIT"

REASONS: Final[tuple[str, ...]] = (
    REASON_DISAGREEMENT,
    REASON_ESCALATION,
    REASON_CALIBRATION_HOLD,
    REASON_AUTONOMY_AUDIT,
    REASON_PENDING_REVIEW,
    REASON_CLAUSE_TRIAGE,
    REASON_ANOMALY,
    REASON_ENTITY_MERGE,
    REASON_PACKET_SPLIT,
)

#: What the console's "Autonomy audits" tab shows: the ARCH-35 work, both the
#: documents calibrated autonomy held back and the ones it sampled.
AUTONOMY_REASONS: Final[tuple[str, ...]] = (
    REASON_AUTONOMY_AUDIT,
    REASON_CALIBRATION_HOLD,
)

#: The view's name. Referenced by the projection and by three gates; a literal
#: in each would be three chances to rename one and not the others.
VIEW_NAME: Final[str] = "review_queue_items"

#: What `POST .../review/bulk` accepts. `resolve` dispatches per kind;
#: `assign` and `unassign` touch only `review_assignments`.
BULK_ACTIONS: Final[tuple[str, ...]] = ("resolve", "assign", "unassign")

MAX_BULK_IDS: Final[int] = 200
MAX_PAGE_SIZE: Final[int] = 100
DEFAULT_PAGE_SIZE: Final[int] = 25


def severity_rank(severity: str) -> int:
    """Rank an unknown severity as least urgent rather than raising.

    A severity the hub has not seen is a source that grew a value; showing it
    at the bottom of the queue is a better failure than a 500 on the queue
    every reviewer opens.
    """
    return SEVERITY_RANK.get(severity, len(SEVERITIES) + 1)


__all__ = [
    "BULK_ACTIONS",
    "DEFAULT_PAGE_SIZE",
    "KINDS",
    "KIND_ANOMALY",
    "KIND_ASSERTION",
    "KIND_EXTRACTION",
    "KIND_MERGE",
    "KIND_SPLIT",
    "MAX_BULK_IDS",
    "MAX_PAGE_SIZE",
    "AUTONOMY_REASONS",
    "REASONS",
    "SEVERITIES",
    "SEVERITY_CRITICAL",
    "SEVERITY_HIGH",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "SEVERITY_RANK",
    "STATUSES",
    "STATUS_OPEN",
    "STATUS_RESOLVED",
    "VIEW_NAME",
    "severity_rank",
]
