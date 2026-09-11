"""RH-5 — recurring maintenance job producer with advisory lock deduplication."""

from __future__ import annotations

import logging
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol, Sequence

from sqlalchemy import select, text

logger = logging.getLogger("app.workers.scheduler")

ADVISORY_LOCK_NAMESPACE = 0x464C5053  # "FLPS"


class ShutdownFlag(Protocol):
    requested: bool


@dataclass(frozen=True)
class ScheduledJob:
    job_type: str
    interval_seconds: int
    at_hour: Optional[int] = None
    at_minute: int = 0
    payload: Optional[dict[str, Any]] = None
    max_attempts: int = 3
    description: str = ""

    def bucket(self, now: datetime) -> str:
        if self.interval_seconds >= 86_400 and self.at_hour is not None:
            anchor = now.replace(
                hour=self.at_hour, minute=self.at_minute, second=0, microsecond=0
            )
            if now < anchor:
                anchor -= timedelta(days=1)
            return anchor.isoformat()

        epoch = int(now.timestamp())
        floored = epoch - (epoch % self.interval_seconds)
        return datetime.fromtimestamp(floored, tz=timezone.utc).isoformat()

    def is_due(self, now: datetime) -> bool:
        if self.interval_seconds < 86_400 or self.at_hour is None:
            return True
        anchor = now.replace(
            hour=self.at_hour, minute=self.at_minute, second=0, microsecond=0
        )
        return now >= anchor

    def idempotency_key(self, now: datetime) -> str:
        return f"sched:{self.job_type}:{self.bucket(now)}"

    def lock_key(self) -> int:
        return zlib.crc32(self.job_type.encode("utf-8")) & 0x7FFF_FFFF


DEFAULT_SCHEDULE: tuple[ScheduledJob, ...] = (
    ScheduledJob(
        job_type="usage.rollup",
        interval_seconds=900,
        description="Hourly and daily metering rollups (ARCH-14).",
    ),
    ScheduledJob(
        job_type="usage.seal",
        interval_seconds=86_400,
        at_hour=0,
        at_minute=20,
        description="Seal the previous period's usage (ARCH-14).",
    ),
    ScheduledJob(
        job_type="storage.sample",
        interval_seconds=3_600,
        description="Per-tenant object storage sampling (ARCH-10).",
    ),
    ScheduledJob(
        job_type="analytics.warehouse_push",
        interval_seconds=300,
        description="Dispatch due export schedules via sync_service (ARCH-26).",
    ),
    ScheduledJob(
        job_type="domain.verify_dns",
        interval_seconds=900,
        description="Poll pending custom-domain TXT challenges (ARCH-25).",
    ),
    ScheduledJob(
        job_type="tls.renew_sweep",
        interval_seconds=86_400,
        at_hour=2,
        description="Renew custom domain certificates inside renewal window (ARCH-25).",
    ),
    ScheduledJob(
        job_type="partner.rev_share_compute",
        interval_seconds=86_400,
        at_hour=3,
        description="Recompute open-period partner revenue share (ARCH-27).",
    ),
    ScheduledJob(
        job_type="partner.rev_share_seal",
        interval_seconds=86_400,
        at_hour=3,
        at_minute=30,
        description="Seal partner revenue-share periods (ARCH-27).",
    ),
    ScheduledJob(
        job_type="identity.sweep_replay_guard",
        interval_seconds=3_600,
        description="Prune expired SAML replay-guard entries (ARCH-16).",
    ),
    ScheduledJob(
        job_type="identity.sweep_auth_requests",
        interval_seconds=3_600,
        description="Prune expired SAML/OIDC auth requests (ARCH-16).",
    ),
    ScheduledJob(
        job_type="identity.recheck_domains",
        interval_seconds=86_400,
        at_hour=4,
        description="Re-verify claimed identity domains (ARCH-16).",
    ),
    ScheduledJob(
        job_type="identity.purge_assertion_payloads",
        interval_seconds=86_400,
        at_hour=4,
        at_minute=10,
        description="Purge retained SAML assertion payloads (ARCH-16).",
    ),
    ScheduledJob(
        job_type="billing.seat_drift",
        interval_seconds=86_400,
        at_hour=5,
        description="Detect Stripe seat-count drift (ARCH-15).",
    ),
    ScheduledJob(
        job_type="billing.dunning_sweep",
        interval_seconds=86_400,
        at_hour=6,
        description="Advance dunning state for past-due subscriptions (ARCH-15).",
    ),
    ScheduledJob(
        job_type="billing.addon_grace_sweep",
        interval_seconds=900,
        description=(
            "Start, advance and end add-on grace windows; halt custom domains "
            "and export schedules after grace (ARCH-30 D-6)."
        ),
    ),
)


class ScheduleError(RuntimeError):
    pass


def validate_schedule(schedule: Sequence[ScheduledJob]) -> None:
    from app.services.job_service import JOB_HANDLERS
    from app.workers.handlers import register_all

    register_all()

    seen: set[str] = set()
    unknown: list[str] = []
    for entry in schedule:
        if entry.job_type in seen:
            raise ScheduleError(f"duplicate scheduled job_type: {entry.job_type!r}")
        seen.add(entry.job_type)
        if entry.interval_seconds < 1:
            raise ScheduleError(f"{entry.job_type!r}: interval_seconds must be >= 1")
        if entry.at_hour is not None and not 0 <= entry.at_hour <= 23:
            raise ScheduleError(f"{entry.job_type!r}: at_hour must be 0..23")
        if entry.job_type not in JOB_HANDLERS:
            unknown.append(entry.job_type)

    if unknown:
        raise ScheduleError(f"scheduled job types with no registered handler: {sorted(unknown)}")

    from app.workers.profiles import PROFILES

    claimable: set[str] = set()
    for profile in PROFILES.values():
        if profile.job_types is None:
            return
        claimable |= set(profile.job_types)

    unclaimable = sorted(seen - claimable)
    if unclaimable:
        raise ScheduleError(f"scheduled job types that no worker profile will claim: {unclaimable}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(db: Any, entry: ScheduledJob, now: datetime) -> bool:
    from app.models.job import Job
    from app.services import job_service

    key = entry.idempotency_key(now)
    if len(key) > 200:
        raise ScheduleError(f"idempotency key for {entry.job_type!r} exceeds 200 characters")

    locked = db.execute(
        text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
        {"ns": ADVISORY_LOCK_NAMESPACE, "key": entry.lock_key()},
    ).scalar()
    if not locked:
        logger.debug("scheduler.lock_held_elsewhere", extra={"job_type": entry.job_type})
        return False

    existing = db.execute(
        select(Job.id)
        .where(Job.job_type == entry.job_type)
        .where(Job.idempotency_key == key)
        .limit(1)
    ).scalar()
    if existing is not None:
        return False

    job = job_service.enqueue(
        db,
        job_type=entry.job_type,
        payload=dict(entry.payload or {}),
        organization_id=None,
        max_attempts=entry.max_attempts,
        idempotency_key=key,
        require_active_transaction=True,
        propagate_trace=False,
    )
    logger.info(
        "scheduler.enqueued",
        extra={
            "job_type": entry.job_type,
            "job_id": str(job.id),
            "bucket": entry.bucket(now),
        },
    )
    return True


def tick(
    schedule: Sequence[ScheduledJob] = DEFAULT_SCHEDULE,
    *,
    now: Optional[datetime] = None,
) -> int:
    from app.db.session import SessionLocal

    moment = now or _now()
    emitted = 0

    for entry in schedule:
        if not entry.is_due(moment):
            continue
        try:
            with SessionLocal() as db:
                with db.begin():
                    if _emit(db, entry, moment):
                        emitted += 1
        except Exception:  # noqa: BLE001
            logger.exception("scheduler.emit_failed", extra={"job_type": entry.job_type})

    return emitted


def run_scheduler_loop(
    *,
    shutdown: ShutdownFlag,
    tick_seconds: float = 30.0,
    schedule: Sequence[ScheduledJob] = DEFAULT_SCHEDULE,
) -> None:
    validate_schedule(schedule)

    logger.info(
        "scheduler.start",
        extra={
            "tick_seconds": tick_seconds,
            "entries": [entry.job_type for entry in schedule],
        },
    )

    passes = 0
    while not shutdown.requested:
        passes += 1
        try:
            emitted = tick(schedule)
            if emitted:
                logger.info("scheduler.tick", extra={"emitted": emitted, "pass": passes})
        except Exception:  # noqa: BLE001
            logger.exception("scheduler.tick_failed", extra={"pass": passes})

        remaining = tick_seconds
        while remaining > 0 and not shutdown.requested:
            slice_seconds = min(0.5, remaining)
            time.sleep(slice_seconds)
            remaining -= slice_seconds

    logger.info("scheduler.stopped", extra={"passes": passes})


__all__ = [
    "DEFAULT_SCHEDULE",
    "ScheduleError",
    "ScheduledJob",
    "run_scheduler_loop",
    "tick",
    "validate_schedule",
]