"""ARCH-10 Step 1 — the single claim primitive.

Supersedes the three parallel claim/mark/reap implementations that ARCH-09
accreted across Steps 3, 9 and 10 (`claim_batch`, `claim_webhook_deliveries`,
`claim_jobs`). All three were the same SQL with different table names; the only
behavioural difference was that `claim_jobs` silently lacked the per-tenant
fairness cap the other two had.

The generic primitive is `claim_eligible_rows()`. Every queue-shaped table
declares a `QueueSpec` describing its status vocabulary and tenancy column,
and the primitive does the rest.
"""

from __future__ import annotations

import logging
import os
import random
import socket
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
)
from app.models.webhook_delivery import (
    CLAIMABLE_DELIVERY_STATUSES,
    WebhookDelivery,
    WebhookDeliveryStatus,
)

logger = logging.getLogger(__name__)

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


# ============================================================================
# Queue descriptors
# ============================================================================


@dataclass(frozen=True)
class QueueSpec:
    """Everything the claim primitive needs to know about one queue table."""

    name: str
    model: Type[Any]
    claimable_statuses: tuple[Any, ...]
    claimed_status: Any
    failed_status: Any
    #: Tenancy column used for per-org fairness ranking. None disables capping.
    org_column: Optional[str] = "organization_id"
    retry_base_seconds: int = RETRY_BASE_SECONDS

    @property
    def table(self) -> Any:
        return self.model.__table__

    @property
    def claimable_values(self) -> list[str]:
        return [getattr(s, "value", s) for s in self.claimable_statuses]

    @property
    def claimed_value(self) -> str:
        return getattr(self.claimed_status, "value", self.claimed_status)

    @property
    def failed_value(self) -> str:
        return getattr(self.failed_status, "value", self.failed_status)


OUTBOX_QUEUE = QueueSpec(
    name="outbox",
    model=OutboxEvent,
    claimable_statuses=CLAIMABLE_STATUSES,
    claimed_status=OutboxEventStatus.CLAIMED,
    failed_status=OutboxEventStatus.FAILED,
)

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
)

QUEUE_SPECS: dict[str, QueueSpec] = {
    spec.name: spec
    for spec in (OUTBOX_QUEUE, WEBHOOK_DELIVERY_QUEUE, JOBS_QUEUE)
}


# ============================================================================
# Shared helpers
# ============================================================================


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
) -> list[Any]:
    """Rank claimable rows within each tenant, returning only the top N per org."""
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


# ============================================================================
# The primitive
# ============================================================================


def claim_eligible_rows(
    db: Session,
    spec: QueueSpec,
    *,
    worker_id: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    per_org_cap: Optional[int] = None,
) -> list[Any]:
    """Atomically lease up to `batch_size` claimable rows from one queue table."""
    worker = worker_id or worker_identity()
    table = spec.table
    model = spec.model
    now = func.now()

    if per_org_cap is not None:
        if spec.org_column is None:
            raise ValueError(
                f"queue {spec.name!r} declares no tenancy column; "
                "per_org_cap is not meaningful."
            )
        eligible_ids = _rank_eligible_ids(
            db, spec, per_org_cap=per_org_cap, batch_size=batch_size
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
    logger.info(
        "%s.claim" % spec.name,
        extra={
            "queue": spec.name,
            "worker": worker,
            "claimed": len(claimed),
            "batch": batch_size,
            "per_org_cap": per_org_cap,
        },
    )
    return list(claimed)


def release_expired_leases(
    db: Session, spec: QueueSpec, *, limit: int = 500
) -> int:
    """Return rows whose lease expired to the claimable pool."""
    table = spec.table

    candidates = (
        select(table.c.id)
        .where(
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


# ============================================================================
# Outbox — result marking
# ============================================================================


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
        logger.warning(
            "outbox.dead_letter",
            extra={"outbox_event_id": str(event_id), "attempts": attempts},
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


# ============================================================================
# Webhook deliveries — result marking
# ============================================================================


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
    logger.warning(
        "webhook_delivery.dead_letter",
        extra={
            "webhook_delivery_id": str(delivery_id),
            "reason": reason,
            "status": response_status,
        },
    )
    return WebhookDeliveryStatus.DEAD


# ============================================================================
# Jobs — result marking
# ============================================================================


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
    logger.warning("jobs.dead_letter", extra={"job_id": str(job_id)})


# ============================================================================
# Deprecated shims — retained so callers continue working unchanged.
# ============================================================================


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
) -> list[Job]:
    return claim_eligible_rows(
        db,
        JOBS_QUEUE,
        worker_id=worker_id,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
        per_org_cap=per_org_cap,
    )


def reap_expired_leases(db: Session, *, limit: int = 500) -> int:
    return release_expired_leases(db, OUTBOX_QUEUE, limit=limit)


def reap_expired_webhook_leases(db: Session, *, limit: int = 500) -> int:
    return release_expired_leases(db, WEBHOOK_DELIVERY_QUEUE, limit=limit)


def reap_expired_job_leases(db: Session, *, limit: int = 500) -> int:
    return release_expired_leases(db, JOBS_QUEUE, limit=limit)