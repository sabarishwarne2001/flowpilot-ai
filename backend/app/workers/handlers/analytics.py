"""ARCH-26 §4 — background handlers for scheduled and manual warehouse syncs.

TWO JOB TYPES, NOT ONE
======================

    analytics.export_sync      run one destination's export now
    analytics.warehouse_push   sweep every due schedule and enqueue the above

The sweeper is separate because it is cross-tenant and the runner is not.
Folding them together would mean the function that reads every tenant's
schedules is the same function that holds one tenant's decrypted credential,
and the blast radius of a mistake in either is then the union of both.

WHY EACH SCHEDULE GETS ITS OWN JOB
==================================

`handle_warehouse_push` enqueues rather than executing inline. A tenant whose
Snowflake is wedged holds a connector call for the control-plane timeout; done
inline, that one tenant delays every other tenant's schedule behind it in the
same tick. Enqueued, they queue independently and the LIGHT fleet drains them
in parallel.

WHY A FAILING RUN RETURNS A RESULT INSTEAD OF RAISING
=====================================================

`execute_sync` records its own failure — run row, audit row, circuit
advance — and returns. Raising here would additionally mark the *job* failed,
which ARCH-09 retries, which produces a second run row for the same window and
a second increment of the failure count. The circuit is the retry policy for
this phase, and it lives in the service.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.db.session import SessionLocal
from app.services import job_service
from app.services.analytics import sync_service

logger = logging.getLogger("app.workers.handlers.analytics")


def handle_export_sync(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one export for one destination.

    Payload:
        organization_id  required
        destination_id   required
        datasets         required, list[str]
        lookback_days    optional, default 1
        schedule_id      optional; present when the run came from a schedule
        trigger          optional, default SCHEDULED
    """
    try:
        organization_id = uuid.UUID(str(payload["organization_id"]))
        destination_id = uuid.UUID(str(payload["destination_id"]))
        datasets = list(payload["datasets"])
    except (KeyError, ValueError, TypeError) as exc:
        raise job_service.JobServiceError(
            "analytics.export_sync payload requires organization_id, "
            "destination_id and datasets."
        ) from exc

    schedule_id = payload.get("schedule_id")
    lookback_days = int(payload.get("lookback_days") or 1)
    trigger = str(payload.get("trigger") or "SCHEDULED")

    with SessionLocal() as db:
        schedule = None
        if schedule_id:
            try:
                schedule = sync_service.get_schedule(
                    db,
                    organization_id=organization_id,
                    schedule_id=uuid.UUID(str(schedule_id)),
                )
            except sync_service.ScheduleNotFoundError:
                # The schedule was deleted between dispatch and execution.
                # The run still happens — the work was already authorised —
                # but it is recorded as a manual run, because there is no
                # cadence left for it to belong to.
                logger.info(
                    "analytics.export_sync.schedule_vanished",
                    extra={"schedule_id": str(schedule_id)},
                )
                trigger = "MANUAL"

        result = sync_service.execute_sync(
            db,
            organization_id=organization_id,
            destination_id=destination_id,
            datasets=datasets,
            lookback_days=lookback_days,
            trigger=trigger,
            schedule=schedule,
        )
        db.commit()

    return {
        "run_id": str(result.run_id),
        "status": result.status,
        "row_count": result.row_count,
        "part_count": result.part_count,
        "bundle_digest": result.bundle_digest,
    }


def handle_warehouse_push(payload: dict[str, Any]) -> dict[str, Any]:
    """Sweep due schedules and enqueue one export job for each.

    `next_run_at` is advanced here, at dispatch, rather than in the runner.
    Advancing it only on success means a destination that fails for a week
    stays permanently due and is re-dispatched on every tick — thousands of
    runs, all failing, which is the silent-retry-forever failure mode
    hardening invariant 5 exists to forbid. The circuit stops the schedule
    being picked up at all after five consecutive failures; between failure
    one and failure five it runs on its normal cadence and no faster.
    """
    enqueued: list[str] = []
    skipped: list[str] = []

    with SessionLocal() as db:
        for schedule in sync_service.due_schedules(db):
            try:
                destination = sync_service.get_destination(
                    db,
                    organization_id=schedule.organization_id,
                    destination_id=schedule.destination_id,
                )
            except sync_service.DestinationNotFoundError:
                skipped.append(str(schedule.id))
                continue

            if not destination.is_active:
                # A disabled destination is the tenant saying "not now". The
                # cadence still advances so that re-enabling it does not
                # trigger an immediate backlog of every missed window.
                skipped.append(str(schedule.id))
            else:
                job_service.enqueue(
                    db,
                    job_type=sync_service.JOB_TYPE_EXPORT_SYNC,
                    payload={
                        "organization_id": str(schedule.organization_id),
                        "destination_id": str(schedule.destination_id),
                        "schedule_id": str(schedule.id),
                        "datasets": list(schedule.datasets or []),
                        "lookback_days": int(schedule.lookback_days or 1),
                        "trigger": "SCHEDULED",
                    },
                    organization_id=schedule.organization_id,
                    # One attempt. `execute_sync` writes its own run row,
                    # audit row and circuit increment on failure, so an
                    # ARCH-09 retry would produce a second run row for the
                    # same window and a second increment. The circuit is the
                    # retry policy for this phase.
                    max_attempts=1,
                )
                enqueued.append(str(schedule.id))

            schedule.next_run_at = sync_service.compute_next_run(
                cadence=schedule.cadence,
                hour_utc=schedule.hour_utc,
                day_of_week=schedule.day_of_week,
                day_of_month=schedule.day_of_month,
            )
            db.flush([schedule])

        db.commit()

    logger.info(
        "analytics.warehouse_push.swept",
        extra={"enqueued": len(enqueued), "skipped": len(skipped)},
    )
    return {"enqueued": enqueued, "skipped": skipped}


__all__ = ["handle_export_sync", "handle_warehouse_push"]