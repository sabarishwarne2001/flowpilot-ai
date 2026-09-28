"""ARCH49-S1:vocabulary — the process-intelligence names, limits and defaults. Pure.

`verify_arch49.py` loads this module without a database or the app settings and
compares it with the migration (sources, object types, actor kinds, SLA object
types, the activity pattern, the retention and hold bounds), with the console
(`types/process.ts`) and with the trigger catalog.

THE EVENT LOG IS OBJECT-CENTRIC
===============================
One event can touch several objects: a posting's failed send touches the
POSTING and the DOCUMENT it was built from; a review decision touches the
REVIEW_ITEM and its DOCUMENT; a case's document arrival touches the CASE and the
DOCUMENT. `process_event_objects` holds those links (OCEL 2.0's event-to-object
relation), so discovery can flatten the log on any object type without choosing
one "case notion" up front.

THE CASE NOTION PER OBJECT TYPE
===============================
  DOCUMENT     a work item: upload, processing, verification, review, posting
  CASE         an ARCH-43 case: opened, documents arriving, evaluations, completion
  POSTING      an ARCH-47 posting: planned, sent, retried, acknowledged or failed
  REVIEW_ITEM  one review-hub item of any of the nine kinds: opened, assigned,
               locked, discussed, proposed by the agent, closed
  FINDING      an ARCH-34 radar finding: raised, confirmed or dismissed
  EXECUTION    an ARCH-37 flow execution: its node runs, in order
"""

from __future__ import annotations

import re
from typing import Final

ENGINE_VERSION: Final[str] = "arch49.1"

# ---------------------------------------------------------------------------
# The event log
# ---------------------------------------------------------------------------

SOURCE_OUTBOX: Final[str] = "OUTBOX"
SOURCE_AUDIT: Final[str] = "AUDIT"
SOURCE_JOB: Final[str] = "JOB"
SOURCE_REVIEW: Final[str] = "REVIEW"
SOURCE_ASSIGNMENT: Final[str] = "ASSIGNMENT"
SOURCE_LOCK: Final[str] = "LOCK"
SOURCE_THREAD: Final[str] = "THREAD"
SOURCE_EXECUTION: Final[str] = "EXECUTION"
SOURCE_NODE_RUN: Final[str] = "NODE_RUN"
SOURCE_CASE: Final[str] = "CASE"
SOURCE_CASE_DOCUMENT: Final[str] = "CASE_DOCUMENT"
SOURCE_CASE_RULE: Final[str] = "CASE_RULE"
SOURCE_POSTING: Final[str] = "POSTING"
SOURCE_POSTING_ATTEMPT: Final[str] = "POSTING_ATTEMPT"
SOURCE_FINDING: Final[str] = "FINDING"
SOURCE_DOCUMENT: Final[str] = "DOCUMENT"
SOURCE_AGENT: Final[str] = "AGENT"
SOURCES: Final[tuple[str, ...]] = (
    SOURCE_OUTBOX, SOURCE_AUDIT, SOURCE_JOB, SOURCE_REVIEW, SOURCE_ASSIGNMENT, SOURCE_LOCK, SOURCE_THREAD,
    SOURCE_EXECUTION, SOURCE_NODE_RUN, SOURCE_CASE, SOURCE_CASE_DOCUMENT, SOURCE_CASE_RULE, SOURCE_POSTING,
    SOURCE_POSTING_ATTEMPT, SOURCE_FINDING, SOURCE_DOCUMENT, SOURCE_AGENT,
)

OBJECT_DOCUMENT: Final[str] = "DOCUMENT"
OBJECT_CASE: Final[str] = "CASE"
OBJECT_POSTING: Final[str] = "POSTING"
OBJECT_REVIEW_ITEM: Final[str] = "REVIEW_ITEM"
OBJECT_FINDING: Final[str] = "FINDING"
OBJECT_EXECUTION: Final[str] = "EXECUTION"
OBJECT_TYPES: Final[tuple[str, ...]] = (
    OBJECT_DOCUMENT, OBJECT_CASE, OBJECT_POSTING, OBJECT_REVIEW_ITEM, OBJECT_FINDING, OBJECT_EXECUTION,
)
OBJECT_LABELS: Final[dict[str, str]] = {
    OBJECT_DOCUMENT: "Documents", OBJECT_CASE: "Cases", OBJECT_POSTING: "ERP postings",
    OBJECT_REVIEW_ITEM: "Review items", OBJECT_FINDING: "Radar findings", OBJECT_EXECUTION: "Flow executions",
}

ACTOR_PERSON: Final[str] = "PERSON"
ACTOR_SYSTEM: Final[str] = "SYSTEM"
ACTOR_AGENT: Final[str] = "AGENT"
ACTOR_KINDS: Final[tuple[str, ...]] = (ACTOR_PERSON, ACTOR_SYSTEM, ACTOR_AGENT)

#: Activities are dotted lower-case names ("posting.attempt.send.transient"). The migration
#: CHECKs the same pattern and length, so no free text (a filename, an error message) can
#: ever be an activity.
ACTIVITY_PATTERN: Final[str] = r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$"
MAX_ACTIVITY_CHARS: Final[int] = 64
_ACTIVITY = re.compile(ACTIVITY_PATTERN)
MAX_SOURCE_KEY_CHARS: Final[int] = 200
MAX_QUALIFIER_CHARS: Final[int] = 32

#: Event attributes carry ids, enums and numbers only -- never document or comment text.
MAX_ATTRIBUTE_KEYS: Final[int] = 16

# -- ingestion -------------------------------------------------------------------------------------

#: Each caught-up run re-reads this much before its watermark: a row whose timestamp (Postgres
#: now() is the transaction's START) was written by a transaction that committed after the
#: previous run read past it is still found. Job leases are 120 s and heartbeat; two hours
#: covers any transaction the platform holds open. The unique (source, source_key) makes the
#: re-read write nothing twice.
OVERLAP_HOURS: Final[int] = 2
#: Rows read per source per run (a run that hits it continues where it stopped next time).
BATCH_LIMIT: Final[int] = 5000
#: The first run reaches back this far.
BACKFILL_DAYS: Final[int] = 180
#: Events older than this are pruned by the sweep (the sources keep their own history; the log
#: is derived and can be rebuilt from what the sources still hold).
RETENTION_DAYS: Final[int] = 400
MIN_RETENTION_DAYS: Final[int] = 30

# -- discovery -------------------------------------------------------------------------------------

DEFAULT_WINDOW_DAYS: Final[int] = 90
MAX_WINDOW_DAYS: Final[int] = 400
MAX_OBJECTS: Final[int] = 20000
MAX_EVENTS: Final[int] = 200000
MAX_VARIANTS: Final[int] = 25
MAX_EDGES: Final[int] = 400

# ---------------------------------------------------------------------------
# SLA prediction
# ---------------------------------------------------------------------------

SLA_OBJECT_TYPES: Final[tuple[str, ...]] = (OBJECT_REVIEW_ITEM, OBJECT_CASE, OBJECT_POSTING)
#: Default targets, in hours, when a workspace has not set its own.
DEFAULT_SLA_HOURS: Final[dict[str, int]] = {OBJECT_REVIEW_ITEM: 24, OBJECT_CASE: 168, OBJECT_POSTING: 4}
MIN_SLA_HOURS: Final[int] = 1
MAX_SLA_HOURS: Final[int] = 24 * 90
DEFAULT_AT_RISK: Final[float] = 0.5
#: The activity that ends an instance of each SLA object type (the first one reached).
TERMINAL_ACTIVITIES: Final[dict[str, tuple[str, ...]]] = {
    OBJECT_REVIEW_ITEM: ("review.closed",),
    OBJECT_CASE: ("case.completed", "case.closed"),
    OBJECT_POSTING: ("posting.acknowledged", "posting.cancelled"),
}

#: The model must see this many finished (or already late) instances to be fitted at all.
MIN_TRAIN_INSTANCES: Final[int] = 40
#: ... and this many held out, of which at least one breached and one did not.
MIN_HOLDOUT_INSTANCES: Final[int] = 10
#: Every fifth instance (by a hash of its id, so a re-run holds out the same ones) is held out,
#: with ALL its snapshots: a snapshot of a held-out instance never leaks into training.
HOLDOUT_EVERY: Final[int] = 5
#: Snapshots taken of each instance's life (evenly across the target window, before its end).
SNAPSHOTS_PER_INSTANCE: Final[int] = 6
#: The fitted model is used only if its held-out Brier score beats the base rate's by at least
#: this much (Brier skill score > MIN_SKILL). A model that is not better than always predicting
#: the historical breach rate predicts nothing.
MIN_SKILL: Final[float] = 0.02
MODEL_MAX_ITER: Final[int] = 150
MODEL_LEARNING_RATE: Final[float] = 0.08
MODEL_MAX_LEAF_NODES: Final[int] = 15
MODEL_RANDOM_STATE: Final[int] = 49
#: Feature names, in the order the model sees them. `kind` and `last_activity` are categorical.
FEATURES: Final[tuple[str, ...]] = (
    "age_ratio", "events", "distinct_activities", "rework", "idle_ratio", "person_events", "agent_events",
    "hour_of_week", "kind", "last_activity",
)
CATEGORICAL_FEATURES: Final[tuple[str, ...]] = ("kind", "last_activity")
RELIABILITY_BINS: Final[int] = 10

PREDICTION_AT_RISK: Final[str] = "AT_RISK"
PREDICTION_OK: Final[str] = "OK"
PREDICTION_BREACHED: Final[str] = "BREACHED"
PREDICTION_STATES: Final[tuple[str, ...]] = (PREDICTION_AT_RISK, PREDICTION_OK, PREDICTION_BREACHED)

RUN_ACCEPTED: Final[str] = "ACCEPTED"
RUN_REFUSED: Final[str] = "REFUSED"
RUN_STATUSES: Final[tuple[str, ...]] = (RUN_ACCEPTED, RUN_REFUSED)

# ---------------------------------------------------------------------------
# Flow Builder and jobs
# ---------------------------------------------------------------------------

#: The one trigger ARCH-49 adds: an open instance predicted to breach its SLA (once per
#: instance and due time: the outbox idempotency key is <event>:<type>:<id>:<due>).
EVENT_SLA_AT_RISK: Final[str] = "trigger.process.sla_at_risk"
TRIGGER_SLA_AT_RISK: Final[str] = "process.sla_at_risk"
JOB_SWEEP: Final[str] = "process.sweep_workspace"
#: The sweep enqueues one job per workspace per slot (idempotency key); the cron runs every 15 min.
SWEEP_SLOT_MINUTES: Final[int] = 15
#: What process intelligence itself writes into the log it reads: the agent's proposal bookkeeping and the
#: SLA alert. They are shown in timelines and mined like anything else, but never features of a prediction:
#: an at-risk alert that became the item's "last activity" would move the next prediction off the very state
#: that raised it (found by the ARCH-49 browser smoke: 0.993 -> 0.349 on the sweep after the alert).
SELF_ACTIVITY_PREFIXES: Final[tuple[str, ...]] = ("agent.", "sla.")


def valid_activity(name: object) -> bool:
    return isinstance(name, str) and 0 < len(name) <= MAX_ACTIVITY_CHARS and bool(_ACTIVITY.match(name))


def activity(*parts: object) -> str:
    """A dotted activity name from parts, each folded to [a-z0-9_]; refuses anything that is not one."""
    cleaned = []
    for part in parts:
        text = re.sub(r"[^a-z0-9_]+", "_", str(part).strip().lower()).strip("_")
        if text:
            cleaned.append(text)
    name = ".".join(cleaned)[:MAX_ACTIVITY_CHARS].rstrip("._")
    if not valid_activity(name):
        raise ValueError(f"not an activity: {parts!r}")
    return name


__all__ = [name for name in dir() if name.isupper() or name in ("activity", "valid_activity")]
