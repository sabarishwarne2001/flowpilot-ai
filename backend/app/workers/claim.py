"""ARCH-09 Step 3 — the claim primitive."""

from __future__ import annotations

import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence, Type

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.models.outbox_event import (
    CLAIMABLE_STATUSES,
    OutboxEvent,
    OutboxEventStatus,
)

logger = logging.getLogger(__name__)

DEFAULT_LEASE_SECONDS: int = 120
DEFAULT_BATCH_SIZE: int = 25

RETRY_BASE_SECONDS: int = 10
RETRY_CEILING_SECONDS: int = 6 * 60 * 60
MAX_ATTEMPTS: int = 12


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _full_jitter_delay(attempts: int) -> timedelta:
    import random

    exponent = max(0, min(attempts, 20))
    window = min(RETRY_CEILING_SECONDS, RETRY_BASE_SECONDS * (2**exponent))
    return timedelta(seconds=random.uniform(0, window))


def claim_batch(
    db: Session,
    *,
    worker_id: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    model: Type[Any] = OutboxEvent,
    claimable_statuses: Sequence[OutboxEventStatus] = CLAIMABLE_STATUSES,
) -> list[Any]:
    worker = worker_id or worker_identity()
    table = model.__table__
    now = func.now()

    candidates = (
        select(table.c.id)
        .where(
            table.c.status.in_([s.value for s in claimable_statuses]),
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
            status=OutboxEventStatus.CLAIMED.value,
            claimed_at=now,
            claimed_by=worker,
            claim_expires_at=now + text(f"interval '{int(lease_seconds)} seconds'"),
            attempts=table.c.attempts + 1,
            updated_at=now,
        )
        .returning(table)
        .execution_options(synchronize_session="fetch")
    )

    rows = db.execute(stmt).fetchall()
    if not rows:
        return []

    ids = [row.id for row in rows]
    claimed = (
        db.execute(select(model).where(model.id.in_(ids))).scalars().all()
    )
    logger.info(
        "outbox.claim",
        extra={"worker": worker, "claimed": len(claimed), "batch": batch_size},
    )
    return list(claimed)


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
        .execution_options(synchronize_session="fetch")
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
            .execution_options(synchronize_session="fetch")
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
        .execution_options(synchronize_session="fetch")
    )
    return OutboxEventStatus.FAILED


def reap_expired_leases(db: Session, *, limit: int = 500) -> int:
    table = OutboxEvent.__table__

    candidates = (
        select(table.c.id)
        .where(
            table.c.status == OutboxEventStatus.CLAIMED.value,
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
            status=OutboxEventStatus.FAILED.value,
            claim_expires_at=None,
            available_at=func.now()
            + text(f"interval '{RETRY_BASE_SECONDS} seconds'"),
            last_error="Lease expired; reclaimed from a worker that did not report a result.",
            updated_at=func.now(),
        )
        .returning(table.c.id)
        .execution_options(synchronize_session="fetch")
    ).fetchall()

    if rows:
        logger.warning("outbox.reaped", extra={"count": len(rows)})
    return len(rows)


def _truncate(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


from app.models.webhook_delivery import (  # noqa: E402
    CLAIMABLE_DELIVERY_STATUSES,
    WebhookDelivery,
    WebhookDeliveryStatus,
)

DELIVERY_MAX_ATTEMPTS: int = MAX_ATTEMPTS
DELIVERY_RETRY_BASE_SECONDS: int = RETRY_BASE_SECONDS
DELIVERY_RETRY_CEILING_SECONDS: int = RETRY_CEILING_SECONDS


def claim_webhook_deliveries(
    db: Session,
    *,
    worker_id: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> list[WebhookDelivery]:
    worker = worker_id or worker_identity()
    table = WebhookDelivery.__table__
    now = func.now()

    candidates = (
        select(table.c.id)
        .where(
            table.c.status.in_([s.value for s in CLAIMABLE_DELIVERY_STATUSES]),
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
            status=WebhookDeliveryStatus.CLAIMED.value,
            claimed_at=now,
            claimed_by=worker,
            claim_expires_at=now + text(f"interval '{int(lease_seconds)} seconds'"),
            attempts=table.c.attempts + 1,
            updated_at=now,
        )
        .returning(table)
        .execution_options(synchronize_session="fetch")
    )

    rows = db.execute(stmt).fetchall()
    if not rows:
        return []

    ids = [row.id for row in rows]
    claimed = (
        db.execute(select(WebhookDelivery).where(WebhookDelivery.id.in_(ids)))
        .scalars()
        .all()
    )
    logger.info(
        "webhook_delivery.claim",
        extra={"worker": worker, "claimed": len(claimed), "batch": batch_size},
    )
    return list(claimed)


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
        .execution_options(synchronize_session="fetch")
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
            .execution_options(synchronize_session="fetch")
        )
        logger.warning(
            "webhook_delivery.dead_letter",
            extra={
                "webhook_delivery_id": str(delivery_id),
                "attempts": attempts,
                "reason": "attempt_ceiling",
            },
        )
        return WebhookDeliveryStatus.DEAD

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
        .execution_options(synchronize_session="fetch")
    )
    return WebhookDeliveryStatus.FAILED


def mark_delivery_dead(
    db: Session,
    delivery_id: uuid.UUID,
    *,
    error: str,
    response_status: Optional[int] = None,
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
        .execution_options(synchronize_session="fetch")
    )
    logger.warning(
        "webhook_delivery.dead_letter",
        extra={
            "webhook_delivery_id": str(delivery_id),
            "reason": "fast_fail",
            "status": response_status,
        },
    )
    return WebhookDeliveryStatus.DEAD


def reap_expired_webhook_leases(db: Session, *, limit: int = 500) -> int:
    table = WebhookDelivery.__table__

    candidates = (
        select(table.c.id)
        .where(
            table.c.status == WebhookDeliveryStatus.CLAIMED.value,
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
            status=WebhookDeliveryStatus.FAILED.value,
            claim_expires_at=None,
            available_at=func.now()
            + text(f"interval '{DELIVERY_RETRY_BASE_SECONDS} seconds'"),
            last_error="Lease expired; reclaimed from a worker that did not report a result.",
            updated_at=func.now(),
        )
        .returning(table.c.id)
        .execution_options(synchronize_session="fetch")
    ).fetchall()

    if rows:
        logger.warning("webhook_delivery.reaped", extra={"count": len(rows)})
    return len(rows)