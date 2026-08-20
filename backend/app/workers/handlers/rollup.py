"""ARCH-14 Step 2 — the `usage.rollup` and `usage.seal` job handlers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.config import settings
from app.core.principal import system_principal
from app.db.session import SessionLocal
from app.services import rollup_service

logger = logging.getLogger("app.workers.handlers.rollup")

ROLLUP_JOB_TYPE = "usage.rollup"
SEAL_JOB_TYPE = "usage.seal"


def _int(payload: dict[str, Any], key: str, default: int) -> int:
    raw = payload.get(key)
    try:
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _now(payload: dict[str, Any]) -> Optional[datetime]:
    raw = payload.get("now")
    if not raw:
        return None
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def handle_usage_rollup(payload: dict[str, Any]) -> dict[str, Any]:
    batch_size = _int(
        payload, "batch_size", getattr(settings, "ROLLUP_BATCH_SIZE", 2_000)
    )
    max_batches = _int(
        payload, "max_batches", getattr(settings, "ROLLUP_MAX_BATCHES", 20)
    )
    now = _now(payload)

    with SessionLocal() as db:
        with system_principal(job_name="jobs.usage.rollup"):
            with db.begin():
                result = rollup_service.run_rollup(
                    db, batch_size=batch_size, max_batches=max_batches, now=now
                )
                remaining = rollup_service.backlog_depth(db)

    outcome = {**result.as_dict(), "backlog_remaining": remaining}

    if remaining > max_batches * batch_size:
        logger.warning("rollup.backlog_not_draining", extra=outcome)
    else:
        logger.info("rollup.job_complete", extra=outcome)

    return outcome


def handle_usage_seal(payload: dict[str, Any]) -> dict[str, Any]:
    grace_hours = _int(
        payload, "grace_hours", getattr(settings, "ROLLUP_SEAL_GRACE_HOURS", 26)
    )
    now = _now(payload)

    with SessionLocal() as db:
        with system_principal(job_name="jobs.usage.seal"):
            with db.begin():
                rolled = rollup_service.run_rollup(db, now=now)
                sealed = rollup_service.seal_due(
                    db, now=now, grace_hours=grace_hours
                )

    outcome = {
        "grace_hours": grace_hours,
        "rolled": rolled.as_dict(),
        **sealed.as_dict(),
    }
    if sealed.skipped:
        logger.warning("rollup.seal_partial", extra=outcome)
    else:
        logger.info("rollup.seal_job_complete", extra=outcome)
    return outcome


def enqueue_rollup(db, *, available_at: Optional[datetime] = None):
    from app.services import job_service

    return job_service.enqueue(
        db,
        job_type=ROLLUP_JOB_TYPE,
        payload={},
        max_attempts=3,
        available_at=available_at,
    )


__all__ = [
    "ROLLUP_JOB_TYPE",
    "SEAL_JOB_TYPE",
    "enqueue_rollup",
    "handle_usage_rollup",
    "handle_usage_seal",
]