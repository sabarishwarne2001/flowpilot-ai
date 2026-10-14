"""Campaign session 4 — server errors counted per minute, for the owner's 5xx alert (§C.4).

The request middleware calls `record_server_error()` for every 5xx answer: a counter in memory,
no I/O on the event loop. The API's periodic flush (`periodic_flush`) adds the counts to
`server_error_minutes`, shared by every API process, and the `ops.alerts` sweep reads the last
few minutes from there.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

_lock = threading.Lock()
_counts: dict[datetime, int] = {}


def _minute(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(second=0, microsecond=0)


def record_server_error(at: datetime | None = None) -> None:
    key = _minute(at or datetime.now(timezone.utc))
    with _lock:
        _counts[key] = _counts.get(key, 0) + 1


def drain() -> dict[datetime, int]:
    global _counts
    with _lock:
        taken, _counts = _counts, {}
    return taken


def flush(db: Any) -> int:
    taken = drain()
    for minute, count in taken.items():
        db.execute(
            text(
                "INSERT INTO server_error_minutes (minute, count) VALUES (:m, :c) "
                "ON CONFLICT (minute) DO UPDATE SET count = server_error_minutes.count + EXCLUDED.count"
            ),
            {"m": minute, "c": count},
        )
    return len(taken)


__all__ = ["drain", "flush", "record_server_error"]
