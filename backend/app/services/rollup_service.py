"""ARCH-14 Step 2 — the rollup engine."""

from __future__ import annotations

import calendar
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.usage_rollup import NIL_UUID, TOTAL_EVENT_TYPE

logger = logging.getLogger("app.services.rollup")

HOUR = "HOUR"
DAY = "DAY"
MONTH = "MONTH"

DETAIL = "DETAIL"
ORG_TOTAL = "ORG_TOTAL"

DEFAULT_BATCH_SIZE: int = 2_000


def hour_bucket(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )


def day_bucket(moment: datetime) -> datetime:
    return hour_bucket(moment).replace(hour=0)


def month_bucket(moment: datetime) -> datetime:
    return day_bucket(moment).replace(day=1)


def bucket_end(granularity: str, start: datetime) -> datetime:
    if granularity == HOUR:
        return start + timedelta(hours=1)
    if granularity == DAY:
        return start + timedelta(days=1)
    if granularity == MONTH:
        days = calendar.monthrange(start.year, start.month)[1]
        return start + timedelta(days=days)
    raise ValueError(f"unknown granularity {granularity!r}")


def bucket_start_for(granularity: str, moment: datetime) -> datetime:
    if granularity == HOUR:
        return hour_bucket(moment)
    if granularity == DAY:
        return day_bucket(moment)
    if granularity == MONTH:
        return month_bucket(moment)
    raise ValueError(f"unknown granularity {granularity!r}")


@dataclass(frozen=True)
class BucketKey:
    organization_id: uuid.UUID
    workspace_id: Optional[uuid.UUID]
    grain: str
    event_type: str
    provider: Optional[str]
    model: Optional[str]
    price_book_id: Optional[uuid.UUID]
    unit_price_micros: Optional[Decimal]


@dataclass
class Delta:
    quantity: Decimal = Decimal(0)
    cost_micros: int = 0
    event_count: int = 0
    estimated_quantity: Decimal = Decimal(0)
    estimated_cost_micros: int = 0
    estimated_event_count: int = 0
    late_event_count: int = 0
    late_quantity: Decimal = Decimal(0)
    late_cost_micros: int = 0
    late_from: dict[str, int] = field(default_factory=dict)

    def add(
        self,
        *,
        quantity: Decimal,
        cost_micros: int,
        estimated: bool,
        late_from: Optional[datetime],
    ) -> None:
        self.quantity += quantity
        self.cost_micros += cost_micros
        self.event_count += 1
        if estimated:
            self.estimated_quantity += quantity
            self.estimated_cost_micros += cost_micros
            self.estimated_event_count += 1
        if late_from is not None:
            self.late_event_count += 1
            self.late_quantity += quantity
            self.late_cost_micros += cost_micros
            iso = late_from.isoformat()
            self.late_from[iso] = self.late_from.get(iso, 0) + 1


@dataclass
class RollupResult:
    claimed: int = 0
    folded: int = 0
    late: int = 0
    buckets_touched: int = 0
    batches: int = 0
    reroutes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "claimed": self.claimed,
            "folded": self.folded,
            "late": self.late,
            "buckets_touched": self.buckets_touched,
            "batches": self.batches,
            "reroutes": self.reroutes,
        }


_CLAIM_SQL = text(
    """
    WITH claimed AS (
        SELECT id
          FROM usage_events
         WHERE aggregated_at IS NULL
         ORDER BY occurred_at, seq
         LIMIT :limit
           FOR UPDATE SKIP LOCKED
    )
    UPDATE usage_events u
       SET aggregated_at = :now
      FROM claimed c
     WHERE u.id = c.id
    RETURNING u.id,
              u.seq,
              u.organization_id,
              u.workspace_id,
              u.event_type,
              u.provider,
              u.details ->> 'model'                              AS model,
              u.price_book_id,
              u.unit_price_micros,
              u.quantity,
              COALESCE(u.cost_micros, 0)                         AS cost_micros,
              u.occurred_at,
              COALESCE((u.details ->> 'estimated')::boolean, false) AS estimated
    """
)


@dataclass(frozen=True)
class ClaimedEvent:
    id: uuid.UUID
    seq: int
    organization_id: uuid.UUID
    workspace_id: Optional[uuid.UUID]
    event_type: str
    provider: Optional[str]
    model: Optional[str]
    price_book_id: Optional[uuid.UUID]
    unit_price_micros: Optional[Decimal]
    quantity: Decimal
    cost_micros: int
    occurred_at: datetime
    estimated: bool


def claim_batch(
    db: Session, *, limit: int, now: datetime
) -> list[ClaimedEvent]:
    rows = db.execute(_CLAIM_SQL, {"limit": int(limit), "now": now}).mappings().all()
    return [
        ClaimedEvent(
            id=row["id"],
            seq=row["seq"],
            organization_id=row["organization_id"],
            workspace_id=row["workspace_id"],
            event_type=row["event_type"],
            provider=row["provider"],
            model=row["model"],
            price_book_id=row["price_book_id"],
            unit_price_micros=(
                Decimal(str(row["unit_price_micros"]))
                if row["unit_price_micros"] is not None
                else None
            ),
            quantity=Decimal(str(row["quantity"])),
            cost_micros=int(row["cost_micros"]),
            occurred_at=row["occurred_at"].astimezone(timezone.utc),
            estimated=bool(row["estimated"]),
        )
        for row in rows
    ]


def _sealed_hours(db: Session, starts: Iterable[datetime]) -> set[datetime]:
    wanted = sorted({s for s in starts})
    if not wanted:
        return set()
    rows = db.execute(
        text(
            "SELECT bucket_start FROM rollup_windows "
            "WHERE granularity = 'HOUR' AND status = 'SEALED' "
            "AND bucket_start = ANY(:starts)"
        ),
        {"starts": wanted},
    ).all()
    return {row[0].astimezone(timezone.utc) for row in rows}


def touch_window(
    db: Session,
    *,
    granularity: str,
    start: datetime,
    now: datetime,
    events: int = 0,
    late_events: int = 0,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO rollup_windows (
                granularity, bucket_start, bucket_end, status,
                first_rolled_at, last_rolled_at, event_count, late_event_count
            )
            VALUES (
                :granularity, :start, :end, 'OPEN',
                :now, :now, :events, :late
            )
            ON CONFLICT (granularity, bucket_start) DO UPDATE SET
                last_rolled_at   = :now,
                event_count      = rollup_windows.event_count
                                   + CASE WHEN rollup_windows.status = 'SEALED'
                                          THEN 0 ELSE EXCLUDED.event_count END,
                late_event_count = rollup_windows.late_event_count
                                   + EXCLUDED.late_event_count,
                updated_at       = :now
            """
        ),
        {
            "granularity": granularity,
            "start": start,
            "end": bucket_end(granularity, start),
            "now": now,
            "events": int(events),
            "late": int(late_events),
        },
    )


_UPSERT_SQL = text(
    f"""
    INSERT INTO usage_rollups (
        organization_id, workspace_id, grain, granularity,
        event_type, provider, model, price_book_id, unit_price_micros,
        bucket_start, bucket_end,
        quantity, cost_micros, event_count,
        estimated_quantity, estimated_cost_micros, estimated_event_count,
        late_event_count, late_quantity, late_cost_micros
    )
    VALUES (
        :organization_id, :workspace_id, :grain, :granularity,
        :event_type, :provider, :model, :price_book_id, :unit_price_micros,
        :bucket_start, :bucket_end,
        :quantity, :cost_micros, :event_count,
        :estimated_quantity, :estimated_cost_micros, :estimated_event_count,
        :late_event_count, :late_quantity, :late_cost_micros
    )
    ON CONFLICT (
        organization_id,
        COALESCE(workspace_id, '{NIL_UUID}'::uuid),
        event_type,
        COALESCE(provider, ''),
        COALESCE(model, ''),
        COALESCE(price_book_id, '{NIL_UUID}'::uuid),
        grain,
        granularity,
        bucket_start
    )
    DO UPDATE SET
        quantity              = usage_rollups.quantity + EXCLUDED.quantity,
        cost_micros           = usage_rollups.cost_micros + EXCLUDED.cost_micros,
        event_count           = usage_rollups.event_count + EXCLUDED.event_count,
        estimated_quantity    = usage_rollups.estimated_quantity
                                + EXCLUDED.estimated_quantity,
        estimated_cost_micros = usage_rollups.estimated_cost_micros
                                + EXCLUDED.estimated_cost_micros,
        estimated_event_count = usage_rollups.estimated_event_count
                                + EXCLUDED.estimated_event_count,
        late_event_count      = usage_rollups.late_event_count
                                + EXCLUDED.late_event_count,
        late_quantity         = usage_rollups.late_quantity
                                + EXCLUDED.late_quantity,
        late_cost_micros      = usage_rollups.late_cost_micros
                                + EXCLUDED.late_cost_micros,
        unit_price_micros     = CASE
            WHEN usage_rollups.unit_price_micros
                 IS DISTINCT FROM EXCLUDED.unit_price_micros
            THEN NULL ELSE usage_rollups.unit_price_micros END,
        details               = CASE
            WHEN usage_rollups.unit_price_micros
                 IS DISTINCT FROM EXCLUDED.unit_price_micros
            THEN COALESCE(usage_rollups.details, '{{}}'::jsonb)
                 || '{{"price_mixed": true}}'::jsonb
            ELSE usage_rollups.details END,
        updated_at            = now()
    RETURNING id
    """
)

_MERGE_LATE_DETAILS_SQL = text(
    """
    UPDATE usage_rollups
       SET details = COALESCE(details, '{}'::jsonb) || jsonb_build_object(
               'late_from_buckets',
               (
                 SELECT jsonb_object_agg(k, total)
                   FROM (
                     SELECT k, sum(v::bigint) AS total
                       FROM (
                         SELECT key AS k, value AS v
                           FROM jsonb_each_text(
                             COALESCE(details -> 'late_from_buckets', '{}'::jsonb)
                           )
                         UNION ALL
                         SELECT key AS k, value AS v
                           FROM jsonb_each_text(CAST(:incoming AS jsonb))
                       ) merged
                      GROUP BY k
                   ) totals
               )
           ),
           updated_at = now()
     WHERE id = :rollup_id
    """
)


def _keys_for(event: ClaimedEvent) -> list[BucketKey]:
    return [
        BucketKey(
            organization_id=event.organization_id,
            workspace_id=event.workspace_id,
            grain=DETAIL,
            event_type=event.event_type,
            provider=event.provider,
            model=event.model,
            price_book_id=event.price_book_id,
            unit_price_micros=event.unit_price_micros,
        ),
        BucketKey(
            organization_id=event.organization_id,
            workspace_id=None,
            grain=ORG_TOTAL,
            event_type=event.event_type,
            provider=None,
            model=None,
            price_book_id=None,
            unit_price_micros=None,
        ),
        BucketKey(
            organization_id=event.organization_id,
            workspace_id=None,
            grain=ORG_TOTAL,
            event_type=TOTAL_EVENT_TYPE,
            provider=None,
            model=None,
            price_book_id=None,
            unit_price_micros=None,
        ),
    ]


def _is_seal_refusal(exc: DBAPIError) -> bool:
    pgcode = getattr(exc.orig, "pgcode", None)
    if pgcode == "42501":
        return True
    diag = getattr(exc.orig, "diag", None)
    if diag is not None and getattr(diag, "sqlstate", None) == "42501":
        return True
    return "is sealed" in str(exc.orig).lower()


def _write_bucket(
    db: Session,
    *,
    key: BucketKey,
    granularity: str,
    start: datetime,
    delta: Delta,
) -> Optional[uuid.UUID]:
    savepoint = db.begin_nested()
    try:
        rollup_id = db.execute(
            _UPSERT_SQL,
            {
                "organization_id": key.organization_id,
                "workspace_id": key.workspace_id,
                "grain": key.grain,
                "granularity": granularity,
                "event_type": key.event_type,
                "provider": key.provider,
                "model": key.model,
                "price_book_id": key.price_book_id,
                "unit_price_micros": key.unit_price_micros,
                "bucket_start": start,
                "bucket_end": bucket_end(granularity, start),
                "quantity": delta.quantity,
                "cost_micros": delta.cost_micros,
                "event_count": delta.event_count,
                "estimated_quantity": delta.estimated_quantity,
                "estimated_cost_micros": delta.estimated_cost_micros,
                "estimated_event_count": delta.estimated_event_count,
                "late_event_count": delta.late_event_count,
                "late_quantity": delta.late_quantity,
                "late_cost_micros": delta.late_cost_micros,
            },
        ).scalar_one()
        savepoint.commit()
        return rollup_id
    except DBAPIError as exc:
        savepoint.rollback()
        if _is_seal_refusal(exc):
            return None
        raise


def _merge_late_details(
    db: Session, *, rollup_id: uuid.UUID, late_from: dict[str, int]
) -> None:
    db.execute(
        _MERGE_LATE_DETAILS_SQL,
        {"rollup_id": rollup_id, "incoming": json.dumps(late_from)},
    )


def fold(
    db: Session, *, events: list[ClaimedEvent], now: datetime, result: RollupResult
) -> set[tuple[uuid.UUID, datetime]]:
    if not events:
        return set()

    open_hour = hour_bucket(now)
    sealed = _sealed_hours(db, (hour_bucket(e.occurred_at) for e in events))

    buckets: dict[tuple[BucketKey, datetime], Delta] = {}
    hour_event_counts: dict[datetime, int] = {}
    hour_late_counts: dict[datetime, int] = {}

    for event in events:
        natural = hour_bucket(event.occurred_at)
        if natural in sealed:
            target = open_hour
            late_from: Optional[datetime] = natural
            result.late += 1
            hour_late_counts[target] = hour_late_counts.get(target, 0) + 1
        else:
            target = natural
            late_from = None

        hour_event_counts[target] = hour_event_counts.get(target, 0) + 1

        for key in _keys_for(event):
            delta = buckets.setdefault((key, target), Delta())
            delta.add(
                quantity=event.quantity,
                cost_micros=event.cost_micros,
                estimated=event.estimated,
                late_from=late_from,
            )
        result.folded += 1

    touched_days: set[tuple[uuid.UUID, datetime]] = set()

    for (key, start), delta in buckets.items():
        rollup_id = _write_bucket(
            db, key=key, granularity=HOUR, start=start, delta=delta
        )

        if rollup_id is None:
            result.reroutes += 1
            delta.late_event_count = delta.event_count
            delta.late_quantity = delta.quantity
            delta.late_cost_micros = delta.cost_micros
            iso = start.isoformat()
            delta.late_from[iso] = delta.late_from.get(iso, 0) + delta.event_count
            logger.warning(
                "rollup.reroute_after_seal",
                extra={
                    "from_bucket": iso,
                    "to_bucket": open_hour.isoformat(),
                    "organization_id": str(key.organization_id),
                    "event_count": delta.event_count,
                },
            )
            rollup_id = _write_bucket(
                db, key=key, granularity=HOUR, start=open_hour, delta=delta
            )
            if rollup_id is None:
                raise RuntimeError(
                    "The current open hour is sealed."
                )
            start = open_hour

        if delta.late_from:
            _merge_late_details(db, rollup_id=rollup_id, late_from=delta.late_from)

        touched_days.add((key.organization_id, day_bucket(start)))
        result.buckets_touched += 1

    for start, count in hour_event_counts.items():
        touch_window(
            db,
            granularity=HOUR,
            start=start,
            now=now,
            events=count,
            late_events=hour_late_counts.get(start, 0),
        )

    return touched_days


_DERIVE_SQL_TEMPLATE = """
    INSERT INTO usage_rollups (
        organization_id, workspace_id, grain, granularity,
        event_type, provider, model, price_book_id, unit_price_micros,
        bucket_start, bucket_end,
        quantity, cost_micros, event_count,
        estimated_quantity, estimated_cost_micros, estimated_event_count,
        late_event_count, late_quantity, late_cost_micros
    )
    SELECT organization_id, workspace_id, grain, :target_granularity,
           event_type, provider, model, price_book_id,
           CASE WHEN min(unit_price_micros) IS DISTINCT FROM max(unit_price_micros)
                THEN NULL ELSE max(unit_price_micros) END,
           :start, :end,
           sum(quantity), sum(cost_micros), sum(event_count),
           sum(estimated_quantity), sum(estimated_cost_micros),
           sum(estimated_event_count),
           sum(late_event_count), sum(late_quantity), sum(late_cost_micros)
      FROM usage_rollups
     WHERE granularity = :source_granularity
       AND organization_id = :organization_id
       AND bucket_start >= :start
       AND bucket_start <  :end
     GROUP BY organization_id, workspace_id, grain, event_type, provider,
              model, price_book_id
    ON CONFLICT (
        organization_id,
        COALESCE(workspace_id, '{nil}'::uuid),
        event_type,
        COALESCE(provider, ''),
        COALESCE(model, ''),
        COALESCE(price_book_id, '{nil}'::uuid),
        grain,
        granularity,
        bucket_start
    )
    DO UPDATE SET
        quantity              = EXCLUDED.quantity,
        cost_micros           = EXCLUDED.cost_micros,
        event_count           = EXCLUDED.event_count,
        estimated_quantity    = EXCLUDED.estimated_quantity,
        estimated_cost_micros = EXCLUDED.estimated_cost_micros,
        estimated_event_count = EXCLUDED.estimated_event_count,
        late_event_count      = EXCLUDED.late_event_count,
        late_quantity         = EXCLUDED.late_quantity,
        late_cost_micros      = EXCLUDED.late_cost_micros,
        unit_price_micros     = EXCLUDED.unit_price_micros,
        updated_at            = now()
      WHERE usage_rollups.sealed_at IS NULL
"""

_DERIVE_SQL = text(_DERIVE_SQL_TEMPLATE.format(nil=NIL_UUID))


def _window_sealed(db: Session, *, granularity: str, start: datetime) -> bool:
    row = db.execute(
        text(
            "SELECT status FROM rollup_windows "
            "WHERE granularity = :g AND bucket_start = :s"
        ),
        {"g": granularity, "s": start},
    ).first()
    return bool(row and row[0] == "SEALED")


def derive(
    db: Session, *, touched_days: set[tuple[uuid.UUID, datetime]], now: datetime
) -> int:
    derived = 0
    touched_months: set[tuple[uuid.UUID, datetime]] = set()

    for organization_id, day in sorted(touched_days, key=lambda t: (str(t[0]), t[1])):
        if _window_sealed(db, granularity=DAY, start=day):
            logger.info(
                "rollup.skip_sealed_day",
                extra={"day": day.isoformat(), "organization_id": str(organization_id)},
            )
        else:
            db.execute(
                _DERIVE_SQL,
                {
                    "target_granularity": DAY,
                    "source_granularity": HOUR,
                    "organization_id": organization_id,
                    "start": day,
                    "end": bucket_end(DAY, day),
                },
            )
            touch_window(db, granularity=DAY, start=day, now=now)
            derived += 1
        touched_months.add((organization_id, month_bucket(day)))

    for organization_id, month in sorted(
        touched_months, key=lambda t: (str(t[0]), t[1])
    ):
        if _window_sealed(db, granularity=MONTH, start=month):
            continue
        db.execute(
            _DERIVE_SQL,
            {
                "target_granularity": MONTH,
                "source_granularity": DAY,
                "organization_id": organization_id,
                "start": month,
                "end": bucket_end(MONTH, month),
            },
        )
        touch_window(db, granularity=MONTH, start=month, now=now)
        derived += 1

    return derived


def run_rollup(
    db: Session,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = 20,
    now: Optional[datetime] = None,
) -> RollupResult:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result = RollupResult()
    touched_days: set[tuple[uuid.UUID, datetime]] = set()

    for _ in range(max_batches):
        events = claim_batch(db, limit=batch_size, now=moment)
        if not events:
            break
        result.claimed += len(events)
        result.batches += 1
        touched_days |= fold(db, events=events, now=moment, result=result)
        if len(events) < batch_size:
            break

    if touched_days:
        derive(db, touched_days=touched_days, now=moment)

    logger.info("rollup.pass_complete", extra=result.as_dict())
    return result


@dataclass
class SealResult:
    sealed: list[tuple[str, datetime]] = field(default_factory=list)
    skipped: list[tuple[str, datetime, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sealed": [[g, s.isoformat()] for g, s in self.sealed],
            "skipped": [[g, s.isoformat(), why] for g, s, why in self.skipped],
        }


def _unaggregated_before(db: Session, moment: datetime) -> int:
    return int(
        db.execute(
            text(
                "SELECT count(*) FROM usage_events "
                "WHERE aggregated_at IS NULL AND occurred_at < :before"
            ),
            {"before": moment},
        ).scalar_one()
    )


def seal_due(
    db: Session,
    *,
    now: Optional[datetime] = None,
    grace_hours: Optional[int] = None,
) -> SealResult:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    grace = int(
        grace_hours
        if grace_hours is not None
        else getattr(settings, "ROLLUP_SEAL_GRACE_HOURS", 26)
    )
    cutoff = moment - timedelta(hours=grace)
    result = SealResult()

    for granularity in (HOUR, DAY, MONTH):
        candidates = db.execute(
            text(
                "SELECT id, bucket_start, bucket_end FROM rollup_windows "
                "WHERE granularity = :g AND status = 'OPEN' AND bucket_end <= :cutoff "
                "ORDER BY bucket_start"
            ),
            {"g": granularity, "cutoff": cutoff},
        ).all()

        for window_id, start, end in candidates:
            start = start.astimezone(timezone.utc)
            end = end.astimezone(timezone.utc)

            backlog = _unaggregated_before(db, end)
            if backlog:
                result.skipped.append(
                    (granularity, start, f"{backlog} unaggregated events precede end")
                )
                logger.warning(
                    "rollup.seal_blocked_by_backlog",
                    extra={
                        "granularity": granularity,
                        "bucket_start": start.isoformat(),
                        "unaggregated": backlog,
                    },
                )
                continue

            if granularity != HOUR:
                open_inside = int(
                    db.execute(
                        text(
                            "SELECT count(*) FROM rollup_windows "
                            "WHERE status = 'OPEN' AND bucket_start >= :start "
                            "AND bucket_end <= :end AND granularity <> :g"
                        ),
                        {"start": start, "end": end, "g": granularity},
                    ).scalar_one()
                )
                if open_inside:
                    result.skipped.append(
                        (granularity, start, f"{open_inside} open sub-windows")
                    )
                    continue

            db.execute(
                text(
                    "UPDATE usage_rollups SET sealed_at = :now, updated_at = :now "
                    "WHERE granularity = :g AND bucket_start = :start "
                    "AND sealed_at IS NULL"
                ),
                {"now": moment, "g": granularity, "start": start},
            )
            db.execute(
                text(
                    "UPDATE rollup_windows "
                    "SET status = 'SEALED', sealed_at = :now, updated_at = :now "
                    "WHERE id = :id"
                ),
                {"now": moment, "id": window_id},
            )
            result.sealed.append((granularity, start))

    logger.info("rollup.seal_complete", extra=result.as_dict())
    return result


def backlog_depth(db: Session) -> int:
    return int(
        db.execute(
            text("SELECT count(*) FROM usage_events WHERE aggregated_at IS NULL")
        ).scalar_one()
    )


__all__ = [
    "DAY",
    "DEFAULT_BATCH_SIZE",
    "DETAIL",
    "HOUR",
    "MONTH",
    "ORG_TOTAL",
    "BucketKey",
    "ClaimedEvent",
    "Delta",
    "RollupResult",
    "SealResult",
    "backlog_depth",
    "bucket_end",
    "bucket_start_for",
    "claim_batch",
    "day_bucket",
    "derive",
    "fold",
    "hour_bucket",
    "month_bucket",
    "run_rollup",
    "seal_due",
    "touch_window",
]
