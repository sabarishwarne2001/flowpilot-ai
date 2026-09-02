"""
ARCH-17 — the in-process observation aggregator.
"""

from __future__ import annotations

import logging
import threading
import uuid
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.request_context import StageRecord, set_stage_sink
from app.core.slo_registry import SLO_REGISTRY, stage_to_slo_key
from app.models.slo import DEFAULT_LATENCY_BOUNDS_MS, SLOUnit

logger = logging.getLogger("app.core.slo_recorder")

FLUSH_INTERVAL_SECONDS: float = 60.0
MAX_TRACKED_SERIES: int = 10_000


def _hour_bucket(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )


@dataclass
class _Series:
    bounds: tuple[float, ...]
    counts: list[int]
    sample_count: int = 0
    error_count: int = 0
    sum_value: float = 0.0

    def observe(self, value: float, *, is_error: bool) -> None:
        self.sample_count += 1
        self.sum_value += value
        if is_error:
            self.error_count += 1
        if self.bounds:
            self.counts[bisect_left(self.bounds, value)] += 1


@dataclass
class _SeriesKey:
    organization_id: str
    slo_key: str
    window_start: datetime


class SLORecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._series: dict[tuple[str, str, datetime], _Series] = {}
        self._dropped_series: int = 0
        self._stage_map = stage_to_slo_key()

    def observe(
        self,
        *,
        organization_id: Optional[Any],
        slo_key: str,
        value: float,
        is_error: bool = False,
        at: Optional[datetime] = None,
    ) -> None:
        if organization_id is None:
            return

        spec = SLO_REGISTRY.get(slo_key)
        if spec is None:
            logger.warning("slo.unknown_key", extra={"slo_key": slo_key})
            return

        bounds = (
            DEFAULT_LATENCY_BOUNDS_MS
            if spec.unit is SLOUnit.MILLISECONDS
            else ()
        )
        key = (str(organization_id), slo_key, _hour_bucket(at or datetime.now(timezone.utc)))

        with self._lock:
            series = self._series.get(key)
            if series is None:
                if len(self._series) >= MAX_TRACKED_SERIES:
                    self._dropped_series += 1
                    return
                series = _Series(bounds=bounds, counts=[0] * (len(bounds) + 1))
                self._series[key] = series
            series.observe(float(value), is_error=is_error)

    def observe_ratio_event(
        self, *, organization_id: Optional[Any], slo_key: str, success: bool
    ) -> None:
        self.observe(
            organization_id=organization_id,
            slo_key=slo_key,
            value=1.0 if success else 0.0,
            is_error=not success,
        )

    def stage_sink(self, record: StageRecord) -> None:
        slo_key = self._stage_map.get(record.name)
        if slo_key is None:
            return
        self.observe(
            organization_id=record.organization_id,
            slo_key=slo_key,
            value=record.elapsed_ms,
            is_error=record.error is not None,
        )

    def drain(self) -> list[tuple[_SeriesKey, _Series]]:
        with self._lock:
            taken = self._series
            dropped = self._dropped_series
            self._series = {}
            self._dropped_series = 0

        if dropped:
            logger.warning(
                "slo.series_dropped",
                extra={"dropped": dropped, "cap": MAX_TRACKED_SERIES},
            )

        return [
            (
                _SeriesKey(
                    organization_id=organization_id,
                    slo_key=slo_key,
                    window_start=window_start,
                ),
                series,
            )
            for (organization_id, slo_key, window_start), series in taken.items()
        ]

    def pending_series(self) -> int:
        with self._lock:
            return len(self._series)


recorder = SLORecorder()


def install() -> None:
    set_stage_sink(recorder.stage_sink)
    logger.info(
        "slo.recorder_installed",
        extra={"stages": sorted(stage_to_slo_key())},
    )


def uninstall() -> None:
    set_stage_sink(None)


_UPSERT_SQL = """
INSERT INTO slo_observations (
    id, organization_id, slo_key, window_start,
    sample_count, error_count, sum_value,
    bucket_bounds, bucket_counts, created_at, updated_at
)
VALUES (
    :id, :organization_id, :slo_key, :window_start,
    :sample_count, :error_count, :sum_value,
    CAST(:bucket_bounds AS jsonb), CAST(:bucket_counts AS jsonb), now(), now()
)
ON CONFLICT (organization_id, slo_key, window_start) DO UPDATE SET
    sample_count = slo_observations.sample_count + EXCLUDED.sample_count,
    error_count  = slo_observations.error_count  + EXCLUDED.error_count,
    sum_value    = slo_observations.sum_value    + EXCLUDED.sum_value,
    bucket_counts = slo_add_bucket_counts(
        slo_observations.bucket_counts,
        slo_observations.bucket_bounds,
        EXCLUDED.bucket_counts,
        EXCLUDED.bucket_bounds
    ),
    updated_at = now()
"""


def flush(db: Any) -> int:
    import json
    from sqlalchemy import text as sa_text

    drained = recorder.drain()
    if not drained:
        return 0

    written = 0
    for key, series in drained:
        if series.sample_count == 0:
            continue
        db.execute(
            sa_text(_UPSERT_SQL),
            {
                "id": str(uuid.uuid4()),
                "organization_id": key.organization_id,
                "slo_key": key.slo_key,
                "window_start": key.window_start,
                "sample_count": series.sample_count,
                "error_count": series.error_count,
                "sum_value": round(series.sum_value, 4),
                "bucket_bounds": json.dumps(list(series.bounds)),
                "bucket_counts": json.dumps(series.counts),
            },
        )
        written += 1

    logger.info("slo.flushed", extra={"series": written})
    return written


__all__ = [
    "FLUSH_INTERVAL_SECONDS",
    "MAX_TRACKED_SERIES",
    "SLORecorder",
    "flush",
    "install",
    "recorder",
    "uninstall",
]
