"""ARCH-12 Step 7 — the `notification.deliver` job handler.

One delivery row, one attempt, one transaction. The retry schedule lives on
the row (`next_attempt_at`, set by `mark_failed`) rather than on the job,
because the delivery is the thing being retried and a job that outlived its
delivery row would retry nothing.

WHY IT RETURNS RATHER THAN RAISES ON A FAILED ATTEMPT
=====================================================

`app/workers/claim.py` treats a raised exception as a job failure and applies
the *job's* backoff. That would give a delivery two competing retry schedules
— the job's and the row's — which drift and produce either duplicate sends or
long stalls. So an attempt that fails is a *successful* job execution that
recorded a failure, and the follow-up attempt is enqueued explicitly with the
row's own `next_attempt_at`. The only thing that raises here is a condition
the job system should genuinely retry: the row could not be read at all.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from app.db.session import SessionLocal
from app.models.notification_delivery import (
    NotificationDelivery,
    NotificationDeliveryStatus,
)
from app.services import job_service
from app.services.notification import outbox_dispatcher

logger = logging.getLogger("app.workers.handlers.notify")


def handle_notification_deliver(payload: dict[str, Any]) -> dict[str, Any]:
    raw_id = payload.get("delivery_id")
    if not raw_id:
        raise ValueError("notification.deliver payload requires 'delivery_id'")

    delivery_id = uuid.UUID(str(raw_id))

    with SessionLocal() as db:
        with db.begin():
            delivery = outbox_dispatcher.claim_delivery(db, delivery_id=delivery_id)
            if delivery is None:
                return {"delivery_id": str(delivery_id), "skipped": "terminal_or_locked"}

            channel = delivery.channel.value
            organization_id = delivery.organization_id
            max_attempts = delivery.max_attempts

            delivered = asyncio.run(outbox_dispatcher.send_delivery(db, delivery))
            status = delivery.status
            attempts = delivery.attempts
            next_attempt_at = delivery.next_attempt_at

            # Schedule the follow-up inside the same transaction that recorded
            # the failure. Outside it, a crash between the two leaves a FAILED
            # row that nothing will ever pick up again.
            if status is NotificationDeliveryStatus.FAILED:
                job_service.enqueue(
                    db,
                    job_type=outbox_dispatcher.JOB_TYPE,
                    payload={"delivery_id": str(delivery_id)},
                    organization_id=organization_id,
                    max_attempts=max_attempts,
                    available_at=next_attempt_at,
                    idempotency_key=f"notify-retry:{delivery_id}:{attempts}",
                )

    result = {
        "delivery_id": str(delivery_id),
        "channel": channel,
        "delivered": delivered,
        "status": status.value,
        "attempts": attempts,
    }
    logger.info("notification.job_complete", extra=result)
    return result


def sweep_due_deliveries(limit: int = 200) -> int:
    """Re-enqueue deliveries whose next attempt is due but has no job.

    Belt to the handler's braces. A job lost to a lease expiry that exhausted
    its own `max_attempts` would otherwise strand a FAILED row forever; this
    runs on the same schedule as `reap_expired_leases` and is served by
    `ix_notification_deliveries_due`.
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    requeued = 0
    with SessionLocal() as db:
        with db.begin():
            due = (
                db.execute(
                    select(NotificationDelivery)
                    .where(
                        NotificationDelivery.status
                        == NotificationDeliveryStatus.FAILED,
                        NotificationDelivery.next_attempt_at
                        <= datetime.now(timezone.utc),
                    )
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            for delivery in due:
                try:
                    job_service.enqueue(
                        db,
                        job_type=outbox_dispatcher.JOB_TYPE,
                        payload={"delivery_id": str(delivery.id)},
                        organization_id=delivery.organization_id,
                        max_attempts=delivery.max_attempts,
                        idempotency_key=(
                            f"notify-sweep:{delivery.id}:{delivery.attempts}"
                        ),
                    )
                    requeued += 1
                except Exception:  # noqa: BLE001
                    # An idempotency collision means a job already exists,
                    # which is the outcome this sweep wants anyway.
                    continue

    if requeued:
        logger.info("notification.sweep_requeued", extra={"count": requeued})
    return requeued


__all__ = ["handle_notification_deliver", "sweep_due_deliveries"]