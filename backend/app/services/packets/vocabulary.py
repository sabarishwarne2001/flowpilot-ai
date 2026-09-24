"""ARCH43-S1:vocabulary — packet dicer constants. Mirrors the migration."""

from __future__ import annotations

from typing import Final

STATUS_PROPOSED: Final = "PROPOSED"
STATUS_SINGLE: Final = "SINGLE"
STATUS_APPROVED: Final = "APPROVED"
STATUS_APPLYING: Final = "APPLYING"
STATUS_APPLIED: Final = "APPLIED"
STATUS_REJECTED: Final = "REJECTED"
STATUS_FAILED: Final = "FAILED"
STATUS_SUPERSEDED: Final = "SUPERSEDED"
STATUSES: Final = (STATUS_PROPOSED, STATUS_SINGLE, STATUS_APPROVED, STATUS_APPLYING, STATUS_APPLIED,
                   STATUS_REJECTED, STATUS_FAILED, STATUS_SUPERSEDED)
LIVE_STATUSES: Final = (STATUS_PROPOSED, STATUS_SINGLE, STATUS_APPROVED, STATUS_APPLYING, STATUS_APPLIED)
DECIDED_STATUSES: Final = (STATUS_APPROVED, STATUS_APPLYING, STATUS_APPLIED)

SOURCE_MODEL: Final = "MODEL"
SOURCE_REVIEWER: Final = "REVIEWER"

VERDICT_APPROVE: Final = "APPROVE"
VERDICT_REJECT: Final = "REJECT"
SPLIT_VERDICTS: Final = (VERDICT_APPROVE, VERDICT_REJECT)

JOB_DETECT: Final = "packets.detect_boundaries"
JOB_APPLY: Final = "packets.apply_split"

#: Detection runs automatically after enrichment only for PDFs of at least
#: this many pages that are not themselves children of a split.
MIN_PAGES_AUTO: Final = 3
#: A plan is refused past this (the planner is linear, the review UI is not).
MAX_PAGES: Final = 2000
#: Boundary probability at or above which a page starts a new document.
DEFAULT_THRESHOLD: Final = 0.5
PDF_MIME: Final = "application/pdf"
