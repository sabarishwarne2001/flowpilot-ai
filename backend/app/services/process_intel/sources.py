"""ARCH49-S1:sources — what already records activity, read into object-centric events.

Every source is read the same way: a UNION of the timestamps it carries, each
row tagged with a key unique within the source, paged by keyset on (ts, key)
and bounded to one workspace. A row is turned into zero or more `EventRow`s,
each with a `source_key` that is unique across all time (the row's id and the
activity), so re-reading never writes an event twice.

WHAT EACH SOURCE CONTRIBUTES (the case notion is chosen later, per object type)
==============================================================================
  DOCUMENT         work_items: uploaded, the pipeline stage reached; retention
                   holds placed and released
  JOB              jobs about a document or a posting: enqueued, succeeded, failed
  OUTBOX           INTERNAL events the state tables do not already record:
                   enriched, verified / disagreed, field changed, reprocessed,
                   completed / failed, redacted, split, table flagged, clause
                   held, case inconsistent, posting exception, SLA at risk
  AUDIT            review decisions through the hub (who decided), lock breaks
  REVIEW           the hub's nine kinds (through review.projection, the view's
                   only reader): opened, closed -- with who closed it
  ASSIGNMENT       review_assignments: assigned (to whom)
  LOCK             review_locks alive when read: locked (by whom)
  THREAD           review_threads: discussion opened, resolved
  EXECUTION        automation_executions: started, and how it ended
  NODE_RUN         automation_node_runs: each node, its type and outcome
  CASE             cases: opened, evaluated (per revision), completed, closed
  CASE_DOCUMENT    case_documents: a document joined a case
  CASE_RULE        case_rule_results: a rule failed (kept here, because the
                   table is rewritten on every evaluation)
  POSTING          erp_postings: planned, delivered, acknowledged, reviewed,
                   cancelled
  POSTING_ATTEMPT  erp_posting_attempts: each render / send / probe / ack /
                   review attempt and its outcome class
  FINDING          anomaly_findings: raised, confirmed / dismissed
  AGENT            agent_proposals: proposed, applied, auto-applied, rejected,
                   undone, superseded, failed

NO TEXT CROSSES INTO THE LOG
============================
Attributes are ids, enum values (states, kinds, layers, outcome classes), slot
and rule keys a template's author configured, and numbers. Error messages,
filenames, extracted values, comment bodies and headlines are never read.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.process_intel import vocabulary as v

UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
_UUID = re.compile(UUID_RE)
_ENUM = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


@dataclass
class EventRow:
    source: str
    source_key: str
    activity: str
    occurred_at: datetime
    actor_kind: str = v.ACTOR_SYSTEM
    actor_user_id: Optional[uuid.UUID] = None
    attributes: dict[str, Any] = field(default_factory=dict)
    objects: list[tuple[str, uuid.UUID, str]] = field(default_factory=list)

    def as_row(self, *, event_id: uuid.UUID, organization_id: uuid.UUID, workspace_id: uuid.UUID) -> dict[str, Any]:
        return {"id": event_id, "organization_id": organization_id, "workspace_id": workspace_id,
                "source": self.source, "source_key": self.source_key[: v.MAX_SOURCE_KEY_CHARS],
                "activity": self.activity, "occurred_at": self.occurred_at, "actor_kind": self.actor_kind,
                "actor_user_id": self.actor_user_id, "attributes": self.attributes}


def as_uuid(value: Any) -> Optional[uuid.UUID]:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str) and _UUID.match(value):
        return uuid.UUID(value)
    return None


def enum_value(value: Any) -> Optional[str]:
    """An enum-like token (a state, a kind, a key an author configured); anything else is dropped."""
    if value is None:
        return None
    raw = getattr(value, "value", value)
    text_value = str(raw)
    return text_value if _ENUM.match(text_value) else None


def attrs(**values: Any) -> dict[str, Any]:
    """Only ids (as strings), enum tokens, booleans and finite numbers survive."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, (int, float)):
            out[key] = value
        elif isinstance(value, uuid.UUID):
            out[key] = str(value)
        else:
            token = enum_value(value)
            if token is not None:
                out[key] = token
        if len(out) >= v.MAX_ATTRIBUTE_KEYS:
            break
    return out


def _objects(*items: tuple[str, Any, str]) -> list[tuple[str, uuid.UUID, str]]:
    seen: dict[tuple[str, uuid.UUID], str] = {}
    for object_type, object_id, qualifier in items:
        oid = as_uuid(object_id)
        if oid is not None and (object_type, oid) not in seen:
            seen[(object_type, oid)] = qualifier
    return [(t, i, q) for (t, i), q in seen.items()]


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------


@dataclass
class Page:
    rows: list[Any]
    last_ts: Optional[datetime]
    last_key: str


def _page(db: Session, union_sql: str, *, workspace_id: uuid.UUID, since: datetime, since_key: str,
          until: datetime, limit: int, extra: Optional[dict[str, Any]] = None) -> Page:
    sql = (f"SELECT * FROM ({union_sql}) s WHERE s.ts IS NOT NULL AND s.ts <= :until "
           "AND (s.ts > :since OR (s.ts = :since AND s.k > :since_key)) ORDER BY s.ts, s.k LIMIT :limit")
    params = {"ws": workspace_id, "since": since, "since_key": since_key, "until": until, "limit": limit,
              **(extra or {})}
    rows = db.execute(text(sql), params).mappings().all()
    return Page(rows=list(rows), last_ts=rows[-1]["ts"] if rows else None, last_key=rows[-1]["k"] if rows else "")


# ---------------------------------------------------------------------------
# The sources
# ---------------------------------------------------------------------------

_DOCUMENT_SQL = """
    SELECT wi.created_at AS ts, wi.id::text || '/uploaded' AS k, wi.id AS id, 'uploaded' AS what,
           wi.created_by_user_id AS actor, wi.parent_work_item_id AS parent, NULL::text AS stage
      FROM work_items wi WHERE wi.workspace_id = :ws
    UNION ALL
    SELECT wi.stage_updated_at, wi.id::text || '/stage/' || wi.pipeline_stage::text, wi.id, 'stage', NULL, NULL,
           wi.pipeline_stage::text
      FROM work_items wi WHERE wi.workspace_id = :ws AND wi.stage_updated_at IS NOT NULL
    UNION ALL
    SELECT h.placed_at, h.id::text || '/hold', h.work_item_id, 'hold_placed', h.placed_by_user_id, NULL, NULL
      FROM retention_holds h WHERE h.workspace_id = :ws AND h.work_item_id IS NOT NULL
    UNION ALL
    SELECT h.released_at, h.id::text || '/released', h.work_item_id, 'hold_released', h.released_by_user_id, NULL,
           NULL
      FROM retention_holds h WHERE h.workspace_id = :ws AND h.work_item_id IS NOT NULL AND h.released_at IS NOT NULL
"""


def _document(row: Any) -> list[EventRow]:
    what = row["what"]
    name = {"uploaded": "document.uploaded", "hold_placed": "document.hold_placed",
            "hold_released": "document.hold_released"}.get(what)
    if what == "stage":
        name = v.activity("document", "stage", row["stage"] or "unknown")
    objects = _objects((v.OBJECT_DOCUMENT, row["id"], ""), (v.OBJECT_DOCUMENT, row["parent"], "parent"))
    actor = as_uuid(row["actor"])
    return [EventRow(v.SOURCE_DOCUMENT, row["k"], name, row["ts"], v.ACTOR_PERSON if actor else v.ACTOR_SYSTEM, actor,
                     attrs(stage=row["stage"], split_child=bool(row["parent"])), objects)]


_JOB_SQL = f"""
    SELECT j.created_at AS ts, j.id::text || '/enqueued' AS k, j.id AS id, j.job_type, 'enqueued' AS what,
           wi.id AS work_item_id, NULL::uuid AS posting_id, j.attempts
      FROM jobs j JOIN work_items wi
        ON (j.payload->>'work_item_id') ~ '{UUID_RE}' AND wi.id = (j.payload->>'work_item_id')::uuid
     WHERE wi.workspace_id = :ws
    UNION ALL
    SELECT j.succeeded_at, j.id::text || '/succeeded', j.id, j.job_type, 'succeeded', wi.id, NULL::uuid, j.attempts
      FROM jobs j JOIN work_items wi
        ON (j.payload->>'work_item_id') ~ '{UUID_RE}' AND wi.id = (j.payload->>'work_item_id')::uuid
     WHERE wi.workspace_id = :ws AND j.succeeded_at IS NOT NULL
    UNION ALL
    SELECT j.updated_at, j.id::text || '/dead', j.id, j.job_type, 'dead', wi.id, NULL::uuid, j.attempts
      FROM jobs j JOIN work_items wi
        ON (j.payload->>'work_item_id') ~ '{UUID_RE}' AND wi.id = (j.payload->>'work_item_id')::uuid
     WHERE wi.workspace_id = :ws AND j.status::text = 'DEAD'
    UNION ALL
    SELECT j.created_at, j.id::text || '/enqueued', j.id, j.job_type, 'enqueued', p.work_item_id, p.id, j.attempts
      FROM jobs j JOIN erp_postings p
        ON (j.payload->>'posting_id') ~ '{UUID_RE}' AND p.id = (j.payload->>'posting_id')::uuid
     WHERE p.workspace_id = :ws
    UNION ALL
    SELECT j.succeeded_at, j.id::text || '/succeeded', j.id, j.job_type, 'succeeded', p.work_item_id, p.id, j.attempts
      FROM jobs j JOIN erp_postings p
        ON (j.payload->>'posting_id') ~ '{UUID_RE}' AND p.id = (j.payload->>'posting_id')::uuid
     WHERE p.workspace_id = :ws AND j.succeeded_at IS NOT NULL
"""


def _job(row: Any) -> list[EventRow]:
    name = v.activity("job", row["job_type"], row["what"])
    objects = _objects((v.OBJECT_DOCUMENT, row["work_item_id"], ""), (v.OBJECT_POSTING, row["posting_id"], ""))
    return [EventRow(v.SOURCE_JOB, row["k"], name, row["ts"], v.ACTOR_SYSTEM, None,
                     attrs(job_type=row["job_type"], attempts=int(row["attempts"] or 0)), objects)]


#: INTERNAL outbox events whose information no state table keeps: event type -> (activity, object type).
OUTBOX_ACTIVITIES: dict[str, tuple[str, str]] = {
    "work_item.enriched": ("document.enriched", v.OBJECT_DOCUMENT),
    "work_item.field_changed": ("document.field_changed", v.OBJECT_DOCUMENT),
    "work_item.verification_completed": ("document.verified", v.OBJECT_DOCUMENT),
    "work_item.verification_disagreed": ("document.verification_disagreed", v.OBJECT_DOCUMENT),
    "trigger.work_item.reprocessed": ("document.reprocessed", v.OBJECT_DOCUMENT),
    "trigger.document.completed": ("document.completed", v.OBJECT_DOCUMENT),
    "trigger.document.failed": ("document.failed", v.OBJECT_DOCUMENT),
    "trigger.redaction.completed": ("document.redacted", v.OBJECT_DOCUMENT),
    "trigger.packet.split": ("document.split", v.OBJECT_DOCUMENT),
    "trigger.table.flagged": ("document.table_flagged", v.OBJECT_DOCUMENT),
    "trigger.assertion.held": ("document.clause_held", v.OBJECT_DOCUMENT),
    "trigger.case.inconsistent": ("case.inconsistent", v.OBJECT_CASE),
    "trigger.posting.failed": ("posting.exception", v.OBJECT_POSTING),
    v.EVENT_SLA_AT_RISK: ("sla.at_risk", ""),
}

_OUTBOX_SQL = """
    SELECT e.created_at AS ts, e.id::text AS k, e.id AS id, e.event_type, e.resource_id,
           e.payload->>'work_item_id' AS work_item_id, e.payload->>'object_type' AS object_type,
           e.payload->>'object_id' AS object_id
      FROM outbox_events e
     WHERE e.workspace_id = :ws AND e.visibility::text = 'INTERNAL' AND e.event_type = ANY(:types)
"""


def _outbox(row: Any) -> list[EventRow]:
    mapped = OUTBOX_ACTIVITIES.get(row["event_type"])
    if mapped is None:
        return []
    name, object_type = mapped
    if row["event_type"] == v.EVENT_SLA_AT_RISK:
        object_type = row["object_type"] if row["object_type"] in v.OBJECT_TYPES else ""
        primary = as_uuid(row["object_id"])
    elif object_type == v.OBJECT_DOCUMENT:
        primary = as_uuid(row["work_item_id"]) or as_uuid(row["resource_id"])
    else:
        primary = as_uuid(row["resource_id"])
    if not object_type or primary is None:
        return []
    objects = _objects((object_type, primary, ""), (v.OBJECT_DOCUMENT, row["work_item_id"], ""))
    return [EventRow(v.SOURCE_OUTBOX, row["k"], name, row["ts"], v.ACTOR_SYSTEM, None,
                     attrs(event_type=row["event_type"]), objects)]


_AUDIT_SQL = """
    SELECT a.created_at AS ts, a.id::text AS k, a.id AS id, a.resource_type::text AS resource_type,
           a.actor_id AS actor, a.resource_id, a.details->>'review_kind' AS review_kind,
           a.details->>'work_item_id' AS work_item_id, a.details->>'operation' AS operation,
           a.details->>'review_item_id' AS review_item_id
      FROM audit_logs a
     WHERE a.workspace_id = :ws AND a.outcome::text = 'ALLOWED'
       AND (a.resource_type::text = 'REVIEW_ITEM'
            OR (a.resource_type::text = 'WORKSPACE' AND a.details->>'operation' = 'review_lock_broken'))
"""


def _audit(row: Any) -> list[EventRow]:
    actor = as_uuid(row["actor"])
    if row["resource_type"] == "REVIEW_ITEM":
        objects = _objects((v.OBJECT_REVIEW_ITEM, row["resource_id"], ""), (v.OBJECT_DOCUMENT, row["work_item_id"], ""))
        return [EventRow(v.SOURCE_AUDIT, row["k"], "review.decided", row["ts"],
                         v.ACTOR_PERSON if actor else v.ACTOR_SYSTEM, actor, attrs(kind=row["review_kind"]), objects)]
    objects = _objects((v.OBJECT_REVIEW_ITEM, row["review_item_id"], ""))
    if not objects:
        return []
    return [EventRow(v.SOURCE_AUDIT, row["k"], "review.lock_broken", row["ts"],
                     v.ACTOR_PERSON if actor else v.ACTOR_SYSTEM, actor, {}, objects)]


def _review_fetch(db: Session, *, workspace_id: uuid.UUID, since: datetime, since_key: str, until: datetime,
                  limit: int) -> Page:
    """The hub's nine kinds, through review.projection (the view's only reader)."""
    from app.services.review import projection

    rows = projection.timeline(db, workspace_id=workspace_id, since=since, since_key=since_key, until=until,
                               limit=limit)
    return Page(rows=rows, last_ts=rows[-1]["ts"] if rows else None, last_key=rows[-1]["k"] if rows else "")


def _review(row: Any) -> list[EventRow]:
    opened = row["what"] == "opened"
    actor = None if opened else as_uuid(row["actor"])
    objects = _objects((v.OBJECT_REVIEW_ITEM, row["item_id"], ""), (v.OBJECT_DOCUMENT, row["work_item_id"], ""))
    if row["kind"] == "ANOMALY":
        objects += _objects((v.OBJECT_FINDING, row["item_id"], ""))
    if row["kind"] == "POSTING":
        objects += _objects((v.OBJECT_POSTING, row["item_id"], ""))
    return [EventRow(v.SOURCE_REVIEW, row["k"], "review.opened" if opened else "review.closed", row["ts"],
                     v.ACTOR_PERSON if actor else v.ACTOR_SYSTEM, actor,
                     attrs(kind=row["kind"], severity=row["severity"], reason=row["reason"]), objects)]


_ASSIGNMENT_SQL = """
    SELECT r.assigned_at AS ts, r.id::text || '/' || r.assignee_user_id::text AS k, r.kind, r.item_id,
           r.assignee_user_id, r.assigned_by_user_id AS actor
      FROM review_assignments r WHERE r.workspace_id = :ws
"""


def _assignment(row: Any) -> list[EventRow]:
    actor = as_uuid(row["actor"])
    return [EventRow(v.SOURCE_ASSIGNMENT, row["k"], "review.assigned", row["ts"],
                     v.ACTOR_PERSON if actor else v.ACTOR_SYSTEM, actor,
                     attrs(kind=row["kind"], assignee_user_id=row["assignee_user_id"]),
                     _objects((v.OBJECT_REVIEW_ITEM, row["item_id"], "")))]


_LOCK_SQL = """
    SELECT l.acquired_at AS ts, l.id::text || '/' || l.lease_token::text AS k, l.kind, l.item_id,
           l.holder_user_id AS actor
      FROM review_locks l WHERE l.workspace_id = :ws
"""


def _lock(row: Any) -> list[EventRow]:
    actor = as_uuid(row["actor"])
    return [EventRow(v.SOURCE_LOCK, row["k"], "review.locked", row["ts"], v.ACTOR_PERSON, actor,
                     attrs(kind=row["kind"]), _objects((v.OBJECT_REVIEW_ITEM, row["item_id"], "")))]


_THREAD_SQL = """
    SELECT t.created_at AS ts, t.id::text || '/opened' AS k, 'opened' AS what, t.kind, t.item_id, t.work_item_id,
           t.created_by_user_id AS actor, t.anchor_kind
      FROM review_threads t WHERE t.workspace_id = :ws
    UNION ALL
    SELECT t.resolved_at, t.id::text || '/resolved/' || t.resolved_at::text, 'resolved', t.kind, t.item_id,
           t.work_item_id, t.resolved_by_user_id, t.anchor_kind
      FROM review_threads t WHERE t.workspace_id = :ws AND t.resolved_at IS NOT NULL
"""


def _thread(row: Any) -> list[EventRow]:
    actor = as_uuid(row["actor"])
    name = "review.discussion_opened" if row["what"] == "opened" else "review.discussion_resolved"
    return [EventRow(v.SOURCE_THREAD, row["k"], name, row["ts"], v.ACTOR_PERSON if actor else v.ACTOR_SYSTEM, actor,
                     attrs(kind=row["kind"], anchor=row["anchor_kind"]),
                     _objects((v.OBJECT_REVIEW_ITEM, row["item_id"], ""), (v.OBJECT_DOCUMENT, row["work_item_id"], "")))]


_EXECUTION_SQL = """
    SELECT e.started_at AS ts, e.id::text || '/started' AS k, 'started' AS what, e.id, e.rule_id, e.work_item_id,
           e.status::text AS status, e.depth
      FROM automation_executions e WHERE e.workspace_id = :ws AND e.started_at IS NOT NULL
    UNION ALL
    SELECT e.completed_at, e.id::text || '/ended', 'ended', e.id, e.rule_id, e.work_item_id, e.status::text, e.depth
      FROM automation_executions e WHERE e.workspace_id = :ws AND e.completed_at IS NOT NULL
"""


def _execution(row: Any) -> list[EventRow]:
    name = "automation.started" if row["what"] == "started" else v.activity("automation", row["status"])
    return [EventRow(v.SOURCE_EXECUTION, row["k"], name, row["ts"], v.ACTOR_SYSTEM, None,
                     attrs(rule_id=row["rule_id"], status=row["status"], depth=int(row["depth"] or 0)),
                     _objects((v.OBJECT_EXECUTION, row["id"], ""), (v.OBJECT_DOCUMENT, row["work_item_id"], "")))]


_NODE_RUN_SQL = """
    SELECT COALESCE(n.completed_at, n.started_at, n.created_at) AS ts, n.id::text AS k, n.execution_id, n.node_key,
           n.node_type, n.status::text AS status, n.sequence, n.action_type, n.details->>'matched' AS matched
      FROM automation_node_runs n JOIN automation_executions e ON e.id = n.execution_id
     WHERE e.workspace_id = :ws
"""


def _node_run(row: Any) -> list[EventRow]:
    name = v.activity("flow", row["node_type"], row["status"])
    return [EventRow(v.SOURCE_NODE_RUN, row["k"], name, row["ts"], v.ACTOR_SYSTEM, None,
                     attrs(node_key=row["node_key"], node_type=row["node_type"], status=row["status"],
                           sequence=int(row["sequence"] or 0), action_type=row["action_type"],
                           matched=(row["matched"] == "true") if row["matched"] in ("true", "false") else None),
                     _objects((v.OBJECT_EXECUTION, row["execution_id"], "")))]


_CASE_SQL = """
    SELECT c.created_at AS ts, c.id::text || '/opened' AS k, 'opened' AS what, c.id, c.template_id, c.status,
           c.revision, c.created_by_user_id AS actor
      FROM cases c WHERE c.workspace_id = :ws
    UNION ALL
    SELECT c.evaluated_at, c.id::text || '/evaluated/' || c.revision::text, 'evaluated', c.id, c.template_id, c.status,
           c.revision, NULL
      FROM cases c WHERE c.workspace_id = :ws AND c.evaluated_at IS NOT NULL
    UNION ALL
    SELECT c.completed_at, c.id::text || '/completed/' || c.completed_at::text, 'completed', c.id, c.template_id,
           c.status, c.revision, NULL
      FROM cases c WHERE c.workspace_id = :ws AND c.completed_at IS NOT NULL
    UNION ALL
    SELECT c.closed_at, c.id::text || '/closed', 'closed', c.id, c.template_id, c.status, c.revision, NULL
      FROM cases c WHERE c.workspace_id = :ws AND c.closed_at IS NOT NULL
"""


def _case(row: Any) -> list[EventRow]:
    actor = as_uuid(row["actor"])
    return [EventRow(v.SOURCE_CASE, row["k"], v.activity("case", row["what"]), row["ts"],
                     v.ACTOR_PERSON if actor else v.ACTOR_SYSTEM, actor,
                     attrs(template_id=row["template_id"], status=row["status"], revision=int(row["revision"] or 0)),
                     _objects((v.OBJECT_CASE, row["id"], "")))]


_CASE_DOCUMENT_SQL = """
    SELECT d.added_at AS ts, d.id::text AS k, d.case_id, d.work_item_id, d.document_type, d.source,
           d.added_by_user_id AS actor
      FROM case_documents d WHERE d.workspace_id = :ws
"""


def _case_document(row: Any) -> list[EventRow]:
    actor = as_uuid(row["actor"])
    return [EventRow(v.SOURCE_CASE_DOCUMENT, row["k"], "case.document_added", row["ts"],
                     v.ACTOR_PERSON if actor else v.ACTOR_SYSTEM, actor,
                     attrs(document_type=row["document_type"], via=row["source"]),
                     _objects((v.OBJECT_CASE, row["case_id"], ""), (v.OBJECT_DOCUMENT, row["work_item_id"], "")))]


_CASE_RULE_SQL = """
    SELECT r.evaluated_at AS ts, r.case_id::text || '/' || r.rule_id || '/' || r.evaluated_at::text AS k, r.case_id,
           r.rule_id, r.op
      FROM case_rule_results r WHERE r.workspace_id = :ws AND r.outcome = 'FAIL'
"""


def _case_rule(row: Any) -> list[EventRow]:
    return [EventRow(v.SOURCE_CASE_RULE, row["k"], "case.rule_failed", row["ts"], v.ACTOR_SYSTEM, None,
                     attrs(rule_id=row["rule_id"], op=row["op"]), _objects((v.OBJECT_CASE, row["case_id"], "")))]


_POSTING_SQL = """
    SELECT p.created_at AS ts, p.id::text || '/planned' AS k, 'planned' AS what, p.id, p.work_item_id, p.target_id,
           p.object_kind, p.origin, p.state, p.created_by_user_id AS actor
      FROM erp_postings p WHERE p.workspace_id = :ws
    UNION ALL
    SELECT p.delivered_at, p.id::text || '/delivered/' || p.delivered_at::text, 'delivered', p.id, p.work_item_id,
           p.target_id, p.object_kind, p.origin, p.state, NULL
      FROM erp_postings p WHERE p.workspace_id = :ws AND p.delivered_at IS NOT NULL
    UNION ALL
    SELECT p.acknowledged_at, p.id::text || '/acknowledged', 'acknowledged', p.id, p.work_item_id, p.target_id,
           p.object_kind, p.origin, p.state, NULL
      FROM erp_postings p WHERE p.workspace_id = :ws AND p.acknowledged_at IS NOT NULL
    UNION ALL
    SELECT p.cancelled_at, p.id::text || '/cancelled', 'cancelled', p.id, p.work_item_id, p.target_id, p.object_kind,
           p.origin, p.state, p.reviewed_by_user_id
      FROM erp_postings p WHERE p.workspace_id = :ws AND p.cancelled_at IS NOT NULL
"""


def _posting(row: Any) -> list[EventRow]:
    actor = as_uuid(row["actor"])
    return [EventRow(v.SOURCE_POSTING, row["k"], v.activity("posting", row["what"]), row["ts"],
                     v.ACTOR_PERSON if actor else v.ACTOR_SYSTEM, actor,
                     attrs(target_id=row["target_id"], object_kind=row["object_kind"], origin=row["origin"],
                           state=row["state"]),
                     _objects((v.OBJECT_POSTING, row["id"], ""), (v.OBJECT_DOCUMENT, row["work_item_id"], "")))]


_POSTING_ATTEMPT_SQL = """
    SELECT a.started_at AS ts, a.id::text AS k, a.posting_id, p.work_item_id, a.kind, a.outcome, a.http_status,
           a.seq, a.actor_user_id AS actor
      FROM erp_posting_attempts a JOIN erp_postings p ON p.id = a.posting_id
     WHERE a.workspace_id = :ws
"""


def _posting_attempt(row: Any) -> list[EventRow]:
    actor = as_uuid(row["actor"])
    return [EventRow(v.SOURCE_POSTING_ATTEMPT, row["k"], v.activity("posting", "attempt", row["kind"], row["outcome"]),
                     row["ts"], v.ACTOR_PERSON if actor else v.ACTOR_SYSTEM, actor,
                     attrs(kind=row["kind"], outcome=row["outcome"], seq=int(row["seq"] or 0),
                           http_status=int(row["http_status"]) if row["http_status"] is not None else None),
                     _objects((v.OBJECT_POSTING, row["posting_id"], ""), (v.OBJECT_DOCUMENT, row["work_item_id"], "")))]


_FINDING_SQL = """
    SELECT f.created_at AS ts, f.id::text || '/raised' AS k, 'raised' AS what, f.id, f.subject_work_item_id,
           f.counterpart_work_item_id, f.kind, f.layer, f.severity, f.status, NULL::uuid AS actor
      FROM anomaly_findings f WHERE f.workspace_id = :ws
    UNION ALL
    SELECT f.resolved_at, f.id::text || '/resolved', lower(f.status), f.id, f.subject_work_item_id,
           f.counterpart_work_item_id, f.kind, f.layer, f.severity, f.status, f.resolved_by_user_id
      FROM anomaly_findings f WHERE f.workspace_id = :ws AND f.resolved_at IS NOT NULL
"""


def _finding(row: Any) -> list[EventRow]:
    actor = as_uuid(row["actor"])
    return [EventRow(v.SOURCE_FINDING, row["k"], v.activity("finding", row["what"]), row["ts"],
                     v.ACTOR_PERSON if actor else v.ACTOR_SYSTEM, actor,
                     attrs(kind=row["kind"], layer=row["layer"], severity=row["severity"]),
                     _objects((v.OBJECT_FINDING, row["id"], ""), (v.OBJECT_DOCUMENT, row["subject_work_item_id"], ""),
                              (v.OBJECT_DOCUMENT, row["counterpart_work_item_id"], "counterpart")))]


_AGENT_SQL = """
    SELECT a.created_at AS ts, a.id::text || '/proposed' AS k, 'proposed' AS what, a.id, a.subject_type, a.subject_id,
           a.subject_kind, a.work_item_id, a.proposal_kind, a.status, NULL::uuid AS actor
      FROM agent_proposals a WHERE a.workspace_id = :ws
    UNION ALL
    SELECT COALESCE(a.applied_at, a.decided_at, a.updated_at), a.id::text || '/' || lower(a.status), lower(a.status),
           a.id, a.subject_type, a.subject_id, a.subject_kind, a.work_item_id, a.proposal_kind, a.status,
           COALESCE(a.decided_by_user_id, a.applied_as_user_id)
      FROM agent_proposals a
     WHERE a.workspace_id = :ws AND a.status IN ('APPLIED', 'AUTO_APPLIED', 'REJECTED', 'UNDONE', 'SUPERSEDED', 'FAILED')
    UNION ALL
    SELECT a.updated_at, a.id::text || '/scheduled', 'scheduled', a.id, a.subject_type, a.subject_id, a.subject_kind,
           a.work_item_id, a.proposal_kind, a.status, NULL::uuid
      FROM agent_proposals a WHERE a.workspace_id = :ws AND a.apply_after IS NOT NULL
"""


def _agent(row: Any) -> list[EventRow]:
    what = row["what"]
    person = what in ("applied", "rejected", "undone")
    actor = as_uuid(row["actor"]) if person or what == "auto_applied" else None
    subject_type = v.OBJECT_REVIEW_ITEM if row["subject_type"] == "REVIEW_ITEM" else v.OBJECT_CASE
    return [EventRow(v.SOURCE_AGENT, row["k"], v.activity("agent", what), row["ts"],
                     v.ACTOR_PERSON if person and actor else v.ACTOR_AGENT, actor,
                     attrs(proposal_id=row["id"], proposal_kind=row["proposal_kind"], kind=row["subject_kind"]),
                     _objects((subject_type, row["subject_id"], ""), (v.OBJECT_DOCUMENT, row["work_item_id"], "")))]


@dataclass(frozen=True)
class Source:
    name: str
    build: Callable[[Any], list[EventRow]]
    sql: Optional[str] = None
    fetch: Optional[Callable[..., Page]] = None
    extra: Optional[Callable[[], dict[str, Any]]] = None

    def page(self, db: Session, *, workspace_id: uuid.UUID, since: datetime, since_key: str, until: datetime,
             limit: int) -> Page:
        if self.fetch is not None:
            return self.fetch(db, workspace_id=workspace_id, since=since, since_key=since_key, until=until,
                              limit=limit)
        return _page(db, self.sql or "", workspace_id=workspace_id, since=since, since_key=since_key, until=until,
                     limit=limit, extra=self.extra() if self.extra else None)


SOURCES: tuple[Source, ...] = (
    Source(v.SOURCE_DOCUMENT, _document, _DOCUMENT_SQL),
    Source(v.SOURCE_JOB, _job, _JOB_SQL),
    Source(v.SOURCE_OUTBOX, _outbox, _OUTBOX_SQL, extra=lambda: {"types": list(OUTBOX_ACTIVITIES)}),
    Source(v.SOURCE_AUDIT, _audit, _AUDIT_SQL),
    Source(v.SOURCE_REVIEW, _review, fetch=_review_fetch),
    Source(v.SOURCE_ASSIGNMENT, _assignment, _ASSIGNMENT_SQL),
    Source(v.SOURCE_LOCK, _lock, _LOCK_SQL),
    Source(v.SOURCE_THREAD, _thread, _THREAD_SQL),
    Source(v.SOURCE_EXECUTION, _execution, _EXECUTION_SQL),
    Source(v.SOURCE_NODE_RUN, _node_run, _NODE_RUN_SQL),
    Source(v.SOURCE_CASE, _case, _CASE_SQL),
    Source(v.SOURCE_CASE_DOCUMENT, _case_document, _CASE_DOCUMENT_SQL),
    Source(v.SOURCE_CASE_RULE, _case_rule, _CASE_RULE_SQL),
    Source(v.SOURCE_POSTING, _posting, _POSTING_SQL),
    Source(v.SOURCE_POSTING_ATTEMPT, _posting_attempt, _POSTING_ATTEMPT_SQL),
    Source(v.SOURCE_FINDING, _finding, _FINDING_SQL),
    Source(v.SOURCE_AGENT, _agent, _AGENT_SQL),
)
SOURCES_BY_NAME: dict[str, Source] = {s.name: s for s in SOURCES}

__all__ = ["EventRow", "OUTBOX_ACTIVITIES", "Page", "SOURCES", "SOURCES_BY_NAME", "Source", "as_uuid", "attrs",
           "enum_value"]
