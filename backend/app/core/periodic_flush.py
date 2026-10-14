"""Campaign session 4 (F-328) — write the API's in-memory measurements on an interval.

The request middleware measures every organization-scoped request (latency and availability,
ARCH-17) into `slo_recorder.recorder`. The worker flushed its recorder every few passes; the API
flushed only when it shut down, so nothing it measured reached `slo_observations` while it ran,
and after 10,000 series the recorder dropped every new measurement.

A daemon thread flushes every `slo_recorder.FLUSH_INTERVAL_SECONDS`. It is a thread, not a task
on the event loop, because flushing is blocking database work (the F-212 / F-218 lesson: nothing
blocking runs on the loop). Each gunicorn worker process runs its own; the upsert adds to the
same rows, so several processes measuring one hour sum correctly.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger("app.core.periodic_flush")


def flush_api_measurements() -> None:
    from app.core import ops_signals, slo_recorder
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        slo_recorder.flush(db)
        ops_signals.flush(db)
        db.commit()


class PeriodicFlusher:
    def __init__(self, flush: Callable[[], None], interval: Callable[[], float]) -> None:
        self._flush = flush
        self._interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="api-measurement-flush", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(max(0.05, float(self._interval()))):
            try:
                self._flush()
            except Exception:  # noqa: BLE001 - a failed flush must never stop the next one
                logger.exception("api.measurement_flush_failed")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None


def _interval() -> float:
    from app.core import slo_recorder

    return slo_recorder.FLUSH_INTERVAL_SECONDS


flusher = PeriodicFlusher(flush_api_measurements, _interval)

__all__ = ["PeriodicFlusher", "flush_api_measurements", "flusher"]
