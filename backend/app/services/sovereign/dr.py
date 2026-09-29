"""ARCH-50 — disaster-recovery evidence the application keeps: heartbeats and measured drills.

ARCH50-S1:dr

The recovery itself (pg_basebackup, WAL archiving, restore to a timestamp) is operator tooling --
`scripts/dr_pitr.py`, run by `deploy/bin/flowpilot-sweep` -- because it drives PostgreSQL's own binaries, not the
application. What lives here is what the application can prove about it:

  heartbeats   `beat()` writes one row a minute on the primary (cron: `flowpilot-sweep dr-heartbeat`). After a
               restore to "latest", the newest heartbeat the restored cluster holds, compared with the newest the
               primary wrote, IS the data lost: the measured RPO of a real incident, not a promise.
  drills       every drill (PITR, logical RESTORE, CHAOS) records what it restored, to when, how much was lost
               and how long it took. The operator console shows the latest of each; a PASSED PITR drill must
               carry both measures (a DB CHECK).
"""

from __future__ import annotations

import socket
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import text

HEARTBEAT_RETENTION_DAYS = 7
DRILL_KINDS: tuple[str, ...] = ("PITR", "RESTORE", "CHAOS")
OUTCOMES: tuple[str, ...] = ("PASSED", "FAILED")
#: The objectives the platform states (the 99.9% SLO's recovery objectives); the console compares every measured
#: drill against them.
TARGET_RPO_SECONDS = 300
TARGET_RTO_SECONDS = 3600


def now() -> datetime:
    """ARCH-50's DR clock (the drill gates pin it)."""
    return datetime.now(timezone.utc)


def node_name() -> str:
    return socket.gethostname()[:64] or "unknown"


def beat(db: Any, *, node: Optional[str] = None, at: Optional[datetime] = None) -> datetime:
    moment = at or now()
    db.execute(text("INSERT INTO dr_heartbeats (beat_at, node) VALUES (:t, :n)"), {"t": moment, "n": node or node_name()})
    db.execute(text("DELETE FROM dr_heartbeats WHERE beat_at < :c"),
               {"c": moment - timedelta(days=HEARTBEAT_RETENTION_DAYS)})
    return moment


def latest_heartbeat(db: Any) -> Optional[datetime]:
    return db.execute(text("SELECT max(beat_at) FROM dr_heartbeats")).scalar()


def record_drill(db: Any, *, kind: str, outcome: str, started_at: datetime, finished_at: datetime,
                 target_time: Optional[datetime] = None, recovered_to: Optional[datetime] = None,
                 rpo_seconds: Optional[float] = None, rto_seconds: Optional[float] = None,
                 host: Optional[str] = None, details: Optional[dict[str, Any]] = None) -> uuid.UUID:
    import json

    if kind not in DRILL_KINDS or outcome not in OUTCOMES:
        raise ValueError(f"unknown drill kind/outcome {kind}/{outcome}")
    drill_id = uuid.uuid4()
    db.execute(text(
        "INSERT INTO dr_drills (id, kind, outcome, started_at, finished_at, target_time, recovered_to, rpo_seconds, "
        "rto_seconds, host, details) VALUES (:id, :k, :o, :s, :f, :t, :r, :rpo, :rto, :h, CAST(:d AS jsonb))"),
        {"id": drill_id, "k": kind, "o": outcome, "s": started_at, "f": finished_at, "t": target_time,
         "r": recovered_to, "rpo": None if rpo_seconds is None else Decimal(str(round(float(rpo_seconds), 3))),
         "rto": None if rto_seconds is None else Decimal(str(round(float(rto_seconds), 3))),
         "h": (host or node_name())[:128], "d": json.dumps(details or {}, default=str)})
    return drill_id


def status(db: Any) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for kind in DRILL_KINDS:
        row = db.execute(text(
            "SELECT id, outcome, started_at, finished_at, target_time, recovered_to, rpo_seconds, rto_seconds, host, "
            "details FROM dr_drills WHERE kind = :k ORDER BY finished_at DESC LIMIT 1"), {"k": kind}).mappings().first()
        latest[kind] = dict(row) if row else None
    recent = [dict(r) for r in db.execute(text(
        "SELECT id, kind, outcome, started_at, finished_at, rpo_seconds, rto_seconds, host FROM dr_drills "
        "ORDER BY finished_at DESC LIMIT 20")).mappings().all()]
    beat_at = latest_heartbeat(db)
    pitr = latest.get("PITR")
    return {
        "targets": {"rpo_seconds": TARGET_RPO_SECONDS, "rto_seconds": TARGET_RTO_SECONDS},
        "latest": latest,
        "recent": recent,
        "heartbeat": {"latest": beat_at, "lag_seconds": (now() - beat_at).total_seconds() if beat_at else None},
        "meets_targets": bool(pitr and pitr["outcome"] == "PASSED"
                              and float(pitr["rpo_seconds"] or 0) <= TARGET_RPO_SECONDS
                              and float(pitr["rto_seconds"] or 0) <= TARGET_RTO_SECONDS),
    }


__all__ = ["DRILL_KINDS", "HEARTBEAT_RETENTION_DAYS", "OUTCOMES", "TARGET_RPO_SECONDS", "TARGET_RTO_SECONDS", "beat",
           "latest_heartbeat", "node_name", "now", "record_drill", "status"]
