"""ARCH49-S1:ingest — the object-centric event log, built incrementally and idempotently.

ONE RUN, PER WORKSPACE
======================
Under a transaction-scoped advisory lock (two sweeps of one workspace take
turns), every source is read twice:

  forward   from exactly (watermark, watermark_key): keyset paging on (ts, key),
            so a burst of rows with one timestamp (a bulk insert shares now())
            is read across runs without being skipped or repeated. The cursor
            moves to the last row read, forward only. A full page leaves the
            source "not caught up": the next run continues where this one
            stopped, and every run makes progress.
  overlap   when the source was caught up, the rows in (watermark -
            OVERLAP_HOURS, watermark] are read again: a row whose timestamp
            (Postgres now() is the transaction's START) was written by a
            transaction that committed after an earlier run read past it is
            still found. At most a page; it never moves the cursor.

Each read is at most BATCH_LIMIT rows. Events are inserted with
`ON CONFLICT (source, source_key) DO NOTHING RETURNING id`: a re-read writes
nothing twice, and only the events actually written get their object links.

The run commits nothing: the caller owns the transaction (the sweep commits per
workspace, the API route per request).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.services.process_intel import service
from app.services.process_intel import sources as S
from app.services.process_intel import vocabulary as v

logger = logging.getLogger("app.services.process_intel.ingest")


def _valid(event: S.EventRow) -> bool:
    return (event.source in v.SOURCES and v.valid_activity(event.activity) and event.actor_kind in v.ACTOR_KINDS
            and bool(event.source_key) and event.occurred_at is not None and bool(event.objects)
            and all(t in v.OBJECT_TYPES and len(q) <= v.MAX_QUALIFIER_CHARS for t, _, q in event.objects))


def write_events(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID,
                 events: Iterable[S.EventRow]) -> int:
    """Insert what is new; link the objects of what was written. Returns the number written."""
    from app.models.process_intel import ProcessEvent, ProcessEventObject

    unique: dict[tuple[str, str], S.EventRow] = {}
    for event in events:
        if _valid(event):
            unique.setdefault((event.source, event.source_key[: v.MAX_SOURCE_KEY_CHARS]), event)
    if not unique:
        return 0
    rows = []
    by_id: dict[uuid.UUID, S.EventRow] = {}
    for event in unique.values():
        event_id = uuid.uuid4()
        by_id[event_id] = event
        rows.append(event.as_row(event_id=event_id, organization_id=organization_id, workspace_id=workspace_id))
    table = ProcessEvent.__table__
    written = db.execute(
        pg_insert(table).values(rows).on_conflict_do_nothing(index_elements=["source", "source_key"])
        .returning(table.c.id)
    ).scalars().all()
    links = []
    for event_id in written:
        event = by_id[event_id]
        for object_type, object_id, qualifier in event.objects:
            links.append({"event_id": event_id, "workspace_id": workspace_id, "object_type": object_type,
                          "object_id": object_id, "qualifier": qualifier, "occurred_at": event.occurred_at,
                          "activity": event.activity})
    if links:
        db.execute(pg_insert(ProcessEventObject.__table__).values(links).on_conflict_do_nothing())
    return len(written)


def ingest(db: Session, *, workspace_id: uuid.UUID, organization_id: Optional[uuid.UUID] = None,
           at: Optional[datetime] = None, only: Optional[Iterable[str]] = None,
           limit: int = v.BATCH_LIMIT) -> dict[str, Any]:
    from app.models.process_intel import ProcessIngestCursor

    moment = at or service.now()
    organization_id = organization_id or service.organization_of(db, workspace_id)
    if organization_id is None:
        raise LookupError("workspace not found")
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('arch49-ingest'), hashtext(:w))"), {"w": str(workspace_id)})
    wanted = set(only) if only else None
    report: dict[str, Any] = {"workspace_id": str(workspace_id), "sources": {}, "written": 0}
    for source in S.SOURCES:
        if wanted is not None and source.name not in wanted:
            continue
        cursor = db.get(ProcessIngestCursor, (workspace_id, source.name))
        if cursor is None:
            since, since_key = moment - timedelta(days=v.BACKFILL_DAYS), ""
        else:
            since, since_key = cursor.watermark, cursor.watermark_key
        page = source.page(db, workspace_id=workspace_id, since=since, since_key=since_key, until=moment, limit=limit)
        rows = list(page.rows)
        if cursor is not None and cursor.caught_up:
            overlap = source.page(db, workspace_id=workspace_id, since=cursor.watermark - timedelta(hours=v.OVERLAP_HOURS),
                                  since_key="", until=min(cursor.watermark, moment), limit=limit)
            rows.extend(overlap.rows)
        events: list[S.EventRow] = []
        for row in rows:
            try:
                events.extend(source.build(row))
            except ValueError:
                logger.warning("process.ingest.row_skipped", extra={"source": source.name})
        written = write_events(db, organization_id=organization_id, workspace_id=workspace_id, events=events)
        if cursor is None:
            cursor = ProcessIngestCursor(workspace_id=workspace_id, source=source.name, watermark=since,
                                         watermark_key="", caught_up=True, last_run_at=moment, rows_read=0,
                                         events_written=0)
            db.add(cursor)
        if page.last_ts is not None:
            cursor.watermark, cursor.watermark_key = page.last_ts, page.last_key
        cursor.caught_up = len(page.rows) < limit
        cursor.last_run_at = moment
        cursor.rows_read = len(rows)
        cursor.events_written = written
        report["sources"][source.name] = {"rows": len(rows), "events": len(events), "written": written,
                                          "caught_up": cursor.caught_up}
        report["written"] += written
    db.flush()
    return report


def prune(db: Session, *, workspace_id: uuid.UUID, at: Optional[datetime] = None,
          retention_days: int = v.RETENTION_DAYS) -> int:
    """Events older than the retention window go (their object links cascade)."""
    from app.models.process_intel import ProcessEvent

    days = max(int(retention_days), v.MIN_RETENTION_DAYS)
    cutoff = (at or service.now()) - timedelta(days=days)
    result = db.execute(delete(ProcessEvent).where(ProcessEvent.workspace_id == workspace_id,
                                                   ProcessEvent.occurred_at < cutoff))
    return int(result.rowcount or 0)


def stats(db: Session, *, workspace_id: uuid.UUID) -> dict[str, Any]:
    events = db.execute(text("SELECT count(*), min(occurred_at), max(occurred_at) FROM process_events "
                             "WHERE workspace_id = :w"), {"w": workspace_id}).one()
    objects = dict(db.execute(text("SELECT object_type, count(DISTINCT object_id) FROM process_event_objects "
                                   "WHERE workspace_id = :w GROUP BY object_type"), {"w": workspace_id}).all())
    cursors = db.execute(text("SELECT source, watermark, caught_up, last_run_at, events_written "
                              "FROM process_ingest_cursors WHERE workspace_id = :w ORDER BY source"),
                         {"w": workspace_id}).mappings().all()
    return {"events": int(events[0] or 0), "first_at": events[1], "last_at": events[2],
            "objects": {t: int(objects.get(t, 0)) for t in v.OBJECT_TYPES},
            "sources": [dict(c) for c in cursors]}


__all__ = ["ingest", "prune", "stats", "write_events"]
