"""ARCH-10 Step 8 — the single claim primitive with active execution runner."""

from __future__ import annotations

import logging
import os
import random
import socket
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence, Type

from sqlalchemy import func, select, text, true as sa_true, update
from sqlalchemy.orm import Session

from app.models.job import CLAIMABLE_JOB_STATUSES, Job, JobStatus
from app.models.outbox_event import (
    CLAIMABLE_STATUSES,
    OutboxEvent,
    OutboxEventStatus,
    OutboxVisibility,
)
from app.models.stripe_inbound_event import (
    CLAIMABLE_STRIPE_INBOUND_STATUSES,
    StripeInboundEvent,
    StripeInboundStatus,
)
from app.models.webhook_delivery import (
    CLAIMABLE_DELIVERY_STATUSES,
    WebhookDelivery,
    WebhookDeliveryStatus,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] - %(message)s",
)
logger = logging.getLogger("app.workers.claim")

DEFAULT_LEASE_SECONDS: int = 120
DEFAULT_BATCH_SIZE: int = 25

RETRY_BASE_SECONDS: int = 10
RETRY_CEILING_SECONDS: int = 6 * 60 * 60
MAX_ATTEMPTS: int = 12

DELIVERY_MAX_ATTEMPTS: int = MAX_ATTEMPTS
DELIVERY_RETRY_BASE_SECONDS: int = RETRY_BASE_SECONDS
DELIVERY_RETRY_CEILING_SECONDS: int = RETRY_CEILING_SECONDS
JOB_MAX_ATTEMPTS_DEFAULT: int = 5

_LEASE_EXPIRED_MESSAGE = (
    "Lease expired; reclaimed from a worker that did not report a result."
)


@dataclass(frozen=True)
class QueueSpec:
    name: str
    model: Type[Any]
    claimable_statuses: tuple[Any, ...]
    claimed_status: Any
    failed_status: Any
    org_column: Optional[str] = "organization_id"
    type_column: Optional[str] = None
    retry_base_seconds: int = RETRY_BASE_SECONDS
    row_filter: Optional[tuple[str, str]] = None

    @property
    def table(self) -> Any:
        return self.model.__table__

    @property
    def row_predicate(self) -> Any:
        if self.row_filter is None:
            return sa_true()
        column, value = self.row_filter
        return self.table.c[column] == value

    @property
    def claimable_values(self) -> list[str]:
        return [getattr(s, "value", s) for s in self.claimable_statuses]

    @property
    def claimed_value(self) -> str:
        return getattr(self.claimed_status, "value", self.claimed_status)

    @property
    def failed_value(self) -> str:
        return getattr(self.failed_status, "value", self.failed_status)


OUTBOX_PUBLIC_QUEUE = QueueSpec(
    name="outbox_public",
    model=OutboxEvent,
    claimable_statuses=CLAIMABLE_STATUSES,
    claimed_status=OutboxEventStatus.CLAIMED,
    failed_status=OutboxEventStatus.FAILED,
    row_filter=("visibility", OutboxVisibility.PUBLIC.value),
)

OUTBOX_INTERNAL_QUEUE = QueueSpec(
    name="outbox_internal",
    model=OutboxEvent,
    claimable_statuses=CLAIMABLE_STATUSES,
    claimed_status=OutboxEventStatus.CLAIMED,
    failed_status=OutboxEventStatus.FAILED,
    row_filter=("visibility", OutboxVisibility.INTERNAL.value),
)

OUTBOX_QUEUE = OUTBOX_PUBLIC_QUEUE

WEBHOOK_DELIVERY_QUEUE = QueueSpec(
    name="webhook_delivery",
    model=WebhookDelivery,
    claimable_statuses=CLAIMABLE_DELIVERY_STATUSES,
    claimed_status=WebhookDeliveryStatus.CLAIMED,
    failed_status=WebhookDeliveryStatus.FAILED,
    retry_base_seconds=DELIVERY_RETRY_BASE_SECONDS,
)

JOBS_QUEUE = QueueSpec(
    name="jobs",
    model=Job,
    claimable_statuses=CLAIMABLE_JOB_STATUSES,
    claimed_status=JobStatus.CLAIMED,
    failed_status=JobStatus.FAILED,
    type_column="job_type",
)

STRIPE_INBOUND_QUEUE = QueueSpec(
    name="stripe_inbound",
    model=StripeInboundEvent,
    claimable_statuses=CLAIMABLE_STRIPE_INBOUND_STATUSES,
    claimed_status=StripeInboundStatus.CLAIMED,
    failed_status=StripeInboundStatus.FAILED,
    org_column="organization_id",
    type_column="event_type",
    retry_base_seconds=15,
)

QUEUE_SPECS: dict[str, QueueSpec] = {
    spec.name: spec
    for spec in (
        OUTBOX_PUBLIC_QUEUE,
        OUTBOX_INTERNAL_QUEUE,
        WEBHOOK_DELIVERY_QUEUE,
        JOBS_QUEUE,
        STRIPE_INBOUND_QUEUE,
    )
}


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _full_jitter_delay(attempts: int) -> timedelta:
    exponent = max(0, min(attempts, 20))
    window = min(RETRY_CEILING_SECONDS, RETRY_BASE_SECONDS * (2**exponent))
    return timedelta(seconds=random.uniform(0, window))


def _truncate(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _rank_eligible_ids(
    db: Session,
    spec: QueueSpec,
    *,
    per_org_cap: int,
    batch_size: int,
    type_filter: Any = None,
) -> list[Any]:
    table = spec.table
    org_col = table.c[spec.org_column]
    now = func.now()

    ranked = (
        select(
            table.c.id,
            table.c.available_at,
            table.c.seq,
            func.row_number()
            .over(partition_by=org_col, order_by=(table.c.available_at, table.c.seq))
            .label("rn"),
        )
        .where(
            type_filter if type_filter is not None else sa_true(),
            spec.row_predicate,
            table.c.status.in_(spec.claimable_values),
            table.c.available_at <= now,
        )
        .subquery()
    )
    eligible_stmt = (
        select(ranked.c.id)
        .where(ranked.c.rn <= per_org_cap)
        .order_by(ranked.c.available_at.asc(), ranked.c.seq.asc())
        .limit(batch_size)
    )
    return [row.id for row in db.execute(eligible_stmt).all()]


def claim_eligible_rows(
    db: Session,
    spec: QueueSpec,
    *,
    worker_id: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    per_org_cap: Optional[int] = None,
    job_types: Optional[Sequence[str]] = None,
) -> list[Any]:
    worker = worker_id or worker_identity()
    table = spec.table
    model = spec.model
    now = func.now()

    if job_types is not None:
        if spec.type_column is None:
            raise ValueError(
                f"queue {spec.name!r} has no type column; job_types is not meaningful."
            )
        wanted = list(job_types)
        if not wanted:
            return []
        type_filter = table.c[spec.type_column].in_(wanted)
    else:
        type_filter = sa_true()

    if per_org_cap is not None:
        if spec.org_column is None:
            raise ValueError(
                f"queue {spec.name!r} declares no tenancy column; per_org_cap is not meaningful."
            )
        eligible_ids = _rank_eligible_ids(
            db,
            spec,
            per_org_cap=per_org_cap,
            batch_size=batch_size,
            type_filter=type_filter,
        )
        if not eligible_ids:
            return []
        id_filter = table.c.id.in_(eligible_ids)
    else:
        id_filter = sa_true()

    candidates = (
        select(table.c.id)
        .where(
            id_filter,
            type_filter,
            spec.row_predicate,
            table.c.status.in_(spec.claimable_values),
            table.c.available_at <= now,
        )
        .order_by(table.c.available_at.asc(), table.c.seq.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )

    stmt = (
        update(table)
        .where(table.c.id.in_(candidates))
        .values(
            status=spec.claimed_value,
            claimed_at=now,
            claimed_by=worker,
            claim_expires_at=now + text(f"interval '{int(lease_seconds)} seconds'"),
            attempts=table.c.attempts + 1,
            updated_at=now,
        )
        .returning(table.c.id)
        .execution_options(synchronize_session=False)
    )

    ids = [row.id for row in db.execute(stmt).fetchall()]
    if not ids:
        return []

    claimed = db.execute(select(model).where(model.id.in_(ids))).scalars().all()
    return list(claimed)


def release_expired_leases(
    db: Session, spec: QueueSpec, *, limit: int = 500
) -> int:
    table = spec.table

    candidates = (
        select(table.c.id)
        .where(
            spec.row_predicate,
            table.c.status == spec.claimed_value,
            table.c.claim_expires_at < func.now(),
        )
        .order_by(table.c.claim_expires_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )

    rows = db.execute(
        update(table)
        .where(table.c.id.in_(candidates))
        .values(
            status=spec.failed_value,
            claim_expires_at=None,
            available_at=func.now()
            + text(f"interval '{spec.retry_base_seconds} seconds'"),
            last_error=_LEASE_EXPIRED_MESSAGE,
            updated_at=func.now(),
        )
        .returning(table.c.id)
        .execution_options(synchronize_session=False)
    ).fetchall()

    if rows:
        logger.warning(
            "%s.reaped" % spec.name,
            extra={"queue": spec.name, "count": len(rows)},
        )
    return len(rows)


def mark_published(db: Session, event_id: uuid.UUID) -> None:
    db.execute(
        update(OutboxEvent)
        .where(OutboxEvent.id == event_id)
        .values(
            status=OutboxEventStatus.PUBLISHED,
            published_at=func.now(),
            claim_expires_at=None,
            last_error=None,
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )


def mark_failed(
    db: Session,
    event_id: uuid.UUID,
    *,
    attempts: int,
    error: str,
    retry_after: Optional[timedelta] = None,
) -> OutboxEventStatus:
    if attempts >= MAX_ATTEMPTS:
        db.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .values(
                status=OutboxEventStatus.DEAD,
                claim_expires_at=None,
                last_error=_truncate(error),
                updated_at=func.now(),
            )
            .execution_options(synchronize_session=False)
        )
        return OutboxEventStatus.DEAD

    if retry_after is not None:
        delay = min(retry_after, timedelta(seconds=RETRY_CEILING_SECONDS))
    else:
        delay = _full_jitter_delay(attempts)

    db.execute(
        update(OutboxEvent)
        .where(OutboxEvent.id == event_id)
        .values(
            status=OutboxEventStatus.FAILED,
            claim_expires_at=None,
            available_at=datetime.now(timezone.utc) + delay,
            last_error=_truncate(error),
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    return OutboxEventStatus.FAILED


def mark_delivered(
    db: Session, delivery_id: uuid.UUID, *, response_status: int
) -> None:
    db.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery_id)
        .values(
            status=WebhookDeliveryStatus.DELIVERED,
            delivered_at=func.now(),
            claim_expires_at=None,
            last_error=None,
            last_response_status=response_status,
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )


def mark_delivery_failed(
    db: Session,
    delivery_id: uuid.UUID,
    *,
    attempts: int,
    error: str,
    response_status: Optional[int] = None,
    retry_after: Optional[timedelta] = None,
) -> WebhookDeliveryStatus:
    if attempts >= DELIVERY_MAX_ATTEMPTS:
        return mark_delivery_dead(
            db,
            delivery_id,
            error=error,
            response_status=response_status,
            reason="attempt_ceiling",
        )

    if retry_after is not None:
        delay = min(retry_after, timedelta(seconds=DELIVERY_RETRY_CEILING_SECONDS))
    else:
        delay = _full_jitter_delay(attempts)

    db.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery_id)
        .values(
            status=WebhookDeliveryStatus.FAILED,
            claim_expires_at=None,
            available_at=datetime.now(timezone.utc) + delay,
            last_error=_truncate(error),
            last_response_status=response_status,
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    return WebhookDeliveryStatus.FAILED


def mark_delivery_dead(
    db: Session,
    delivery_id: uuid.UUID,
    *,
    error: str,
    response_status: Optional[int] = None,
    reason: str = "fast_fail",
) -> WebhookDeliveryStatus:
    db.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery_id)
        .values(
            status=WebhookDeliveryStatus.DEAD,
            claim_expires_at=None,
            last_error=_truncate(error),
            last_response_status=response_status,
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    return WebhookDeliveryStatus.DEAD


def mark_job_succeeded(
    db: Session, job_id: uuid.UUID, *, result: Optional[dict] = None
) -> None:
    db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status=JobStatus.SUCCEEDED,
            succeeded_at=func.now(),
            claim_expires_at=None,
            last_error=None,
            result=result,
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )


def mark_job_failed(
    db: Session, job_id: uuid.UUID, *, attempts: int, error: str
) -> None:
    delay = _full_jitter_delay(attempts)
    db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status=JobStatus.FAILED,
            claim_expires_at=None,
            available_at=datetime.now(timezone.utc) + delay,
            last_error=_truncate(error),
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )


def mark_job_dead(db: Session, job_id: uuid.UUID, *, error: str) -> None:
    db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status=JobStatus.DEAD,
            claim_expires_at=None,
            last_error=_truncate(error),
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )


def claim_batch(
    db: Session,
    *,
    worker_id: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    model: Type[Any] = OutboxEvent,
    claimable_statuses: Sequence[Any] = CLAIMABLE_STATUSES,
    per_org_cap: Optional[int] = None,
) -> list[Any]:
    if model is OutboxEvent:
        spec = OUTBOX_QUEUE
    else:
        spec = QueueSpec(
            name=getattr(model, "__tablename__", "custom"),
            model=model,
            claimable_statuses=tuple(claimable_statuses),
            claimed_status=OutboxEventStatus.CLAIMED,
            failed_status=OutboxEventStatus.FAILED,
        )
    return claim_eligible_rows(
        db,
        spec,
        worker_id=worker_id,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
        per_org_cap=per_org_cap,
    )


def claim_webhook_deliveries(
    db: Session,
    *,
    worker_id: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    per_org_cap: Optional[int] = None,
) -> list[WebhookDelivery]:
    return claim_eligible_rows(
        db,
        WEBHOOK_DELIVERY_QUEUE,
        worker_id=worker_id,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
        per_org_cap=per_org_cap,
    )


def claim_jobs(
    db: Session,
    *,
    worker_id: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    per_org_cap: Optional[int] = None,
    job_types: Optional[Sequence[str]] = None,
) -> list[Job]:
    return claim_eligible_rows(
        db,
        JOBS_QUEUE,
        worker_id=worker_id,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
        per_org_cap=per_org_cap,
        job_types=job_types,
    )


def reap_expired_leases(db: Session, *, limit: int = 500) -> int:
    return release_expired_leases(db, OUTBOX_QUEUE, limit=limit)


def reap_expired_webhook_leases(db: Session, *, limit: int = 500) -> int:
    return release_expired_leases(db, WEBHOOK_DELIVERY_QUEUE, limit=limit)


def reap_expired_job_leases(db: Session, *, limit: int = 500) -> int:
    return release_expired_leases(db, JOBS_QUEUE, limit=limit)


def claim_stripe_inbound_events(
    db: Session,
    *,
    worker_id: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> list[StripeInboundEvent]:
    return claim_eligible_rows(
        db,
        STRIPE_INBOUND_QUEUE,
        worker_id=worker_id,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
    )


def reap_expired_stripe_inbound_leases(db: Session, *, limit: int = 500) -> int:
    return release_expired_leases(db, STRIPE_INBOUND_QUEUE, limit=limit)


# ============================================================================
# Continuous Worker Polling & Execution Runner
# ============================================================================


def run_worker_loop(poll_interval: float = 1.0) -> None:
    """Continuously poll, claim, and execute asynchronous background jobs."""
    from app.db.session import SessionLocal
    from app.services import job_service
    from app.workers.handlers import register_all

    registered = register_all()
    identity = worker_identity()

    logger.info(
        "================================================================="
    )
    logger.info(f"FlowPilot AI Background Worker Initialized [{identity}]")
    logger.info(f"Registered {len(registered)} job handlers:")
    for jt in registered:
        logger.info(f"  -> {jt}")
    logger.info(
        "================================================================="
    )

    while True:
        processed_count = 0
        try:
            with SessionLocal() as db:
                # 1. Reap any expired job leases
                reaped = reap_expired_job_leases(db)
                if reaped > 0:
                    logger.warning(f"Reaped {reaped} expired job lease(s).")
                db.commit()

                # 2. Claim pending jobs
                claimed_jobs = claim_jobs(
                    db,
                    worker_id=identity,
                    batch_size=10,
                    lease_seconds=DEFAULT_LEASE_SECONDS,
                )

                if claimed_jobs:
                    db.commit()
                    processed_count += len(claimed_jobs)

                    for job in claimed_jobs:
                        handler = job_service.JOB_HANDLERS.get(job.job_type)
                        if not handler:
                            err = f"No registered handler for job_type '{job.job_type}'"
                            logger.error(err)
                            mark_job_failed(
                                db, job.id, attempts=job.attempts, error=err
                            )
                            db.commit()
                            continue

                        start_t = time.perf_counter()
                        logger.info(
                            f"[CLAIMED] Job {job.id} | Type: '{job.job_type}' | Attempt: {job.attempts}"
                        )

                        try:
                            payload = dict(job.payload or {})
                            payload["job_id"] = str(job.id)
                            result = handler(payload)
                            mark_job_succeeded(db, job.id, result=result)
                            db.commit()
                            dur = round(time.perf_counter() - start_t, 3)
                            logger.info(
                                f"[SUCCESS] Job {job.id} '{job.job_type}' finished in {dur}s."
                            )
                        except Exception as exc:
                            dur = round(time.perf_counter() - start_t, 3)
                            logger.exception(
                                f"[FAILED] Job {job.id} '{job.job_type}' failed after {dur}s: {exc}"
                            )
                            mark_job_failed(
                                db,
                                job.id,
                                attempts=job.attempts,
                                error=str(exc),
                            )
                            db.commit()

        except Exception as exc:
            logger.error(f"Unexpected worker loop exception: {exc}")

        # Sleep briefly if no work was found
        if processed_count == 0:
            time.sleep(poll_interval)


if __name__ == "__main__":
    try:
        run_worker_loop()
    except KeyboardInterrupt:
        logger.info("Worker stopped by user (KeyboardInterrupt).")
        sys.exit(0)