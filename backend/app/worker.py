"""ARCH-09 Step 6b — the worker entrypoint, two loops.

    python -m app.worker --loop relay       # outbox_events -> webhook_deliveries
    python -m app.worker --loop delivery    # webhook_deliveries -> the network
"""

from __future__ import annotations

import argparse
import logging
import random
import signal
import sys
import time
from types import FrameType
from typing import Optional

logger = logging.getLogger("app.worker")

_HEAVY_MODULES = ("paddleocr", "chromadb", "sentence_transformers", "torch")


def assert_no_heavy_imports(*, allow: tuple[str, ...] = ()) -> None:
    """Fail at startup rather than at first OOM."""
    leaked = [
        name for name in _HEAVY_MODULES if name not in allow and name in sys.modules
    ]
    if leaked:
        raise RuntimeError(
            "ARCH-09 §B.8 import isolation violated: "
            f"{', '.join(leaked)} loaded into the worker."
        )


class GracefulShutdown:
    """SIGTERM means 'finish the batch you hold, then stop'."""

    def __init__(self) -> None:
        self.requested = False
        self._first_signal_at: Optional[float] = None

    def install(self) -> "GracefulShutdown":
        signal.signal(signal.SIGTERM, self._handle)
        signal.signal(signal.SIGINT, self._handle)
        return self

    def _handle(self, signum: int, frame: Optional[FrameType]) -> None:
        now = time.monotonic()
        if self.requested and self._first_signal_at is not None:
            logger.warning(
                "worker.shutdown.forced",
                extra={"signal": signum, "waited_s": now - self._first_signal_at},
            )
            raise SystemExit(1)
        self.requested = True
        self._first_signal_at = now
        logger.info("worker.shutdown.requested", extra={"signal": signum})


def _idle_sleep(seconds: float) -> None:
    time.sleep(seconds * random.uniform(0.5, 1.5))


# ======================================================================
# Loop 1 — relay: outbox_events -> webhook_deliveries
# ======================================================================
def run_relay_loop(
    *,
    shutdown: GracefulShutdown,
    batch_size: int,
    lease_seconds: int,
    idle_sleep_seconds: float,
    reap_every_n_passes: int = 20,
) -> None:
    from app.core.principal import system_principal
    from app.db.session import SessionLocal
    from app.services.webhook_service import fan_out_event
    from app.workers.claim import (
        claim_batch,
        mark_failed,
        mark_published,
        reap_expired_leases,
        worker_identity,
    )

    worker = worker_identity()
    logger.info("worker.start", extra={"worker": worker, "loop": "relay"})
    passes = 0

    while not shutdown.requested:
        passes += 1

        if passes % reap_every_n_passes == 0:
            with SessionLocal() as db:
                reaped = reap_expired_leases(db)
                db.commit()
            if reaped:
                logger.warning("relay.reaped", extra={"count": reaped})

        processed = 0
        with SessionLocal() as db:
            claimed = claim_batch(
                db,
                worker_id=worker,
                batch_size=batch_size,
                lease_seconds=lease_seconds,
            )
            for event in claimed:
                event_id, attempts = event.id, event.attempts
                with system_principal(job_name="outbox.relay", job_id=event_id):
                    try:
                        with db.begin_nested():
                            deliveries = fan_out_event(db, event)
                            mark_published(db, event_id)
                        processed += 1
                        logger.info(
                            "relay.fanned_out",
                            extra={
                                "outbox_event_id": str(event_id),
                                "event_type": event.event_type,
                                "deliveries": len(deliveries),
                            },
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception(
                            "relay.event_failed",
                            extra={"outbox_event_id": str(event_id)},
                        )
                        with db.begin_nested():
                            mark_failed(
                                db,
                                event_id,
                                attempts=attempts,
                                error=f"{type(exc).__name__}: {exc}",
                            )
            db.commit()

        if processed == 0:
            _idle_sleep(idle_sleep_seconds)

    logger.info(
        "worker.stopped", extra={"worker": worker, "loop": "relay", "passes": passes}
    )


# ======================================================================
# Loop 2 — delivery: webhook_deliveries -> the network
# ======================================================================
def run_delivery_loop(
    *,
    shutdown: GracefulShutdown,
    batch_size: int,
    lease_seconds: int,
    idle_sleep_seconds: float,
    reap_every_n_passes: int = 20,
) -> None:
    from sqlalchemy import select

    from app.core.principal import system_principal
    from app.db.session import SessionLocal
    from app.models.webhook_delivery import WebhookDelivery
    from app.models.webhook_endpoint import WebhookEndpoint, WebhookEndpointStatus
    from app.services.webhook_dispatch import attempt_delivery, record_outcome
    from app.workers.claim import (
        claim_webhook_deliveries,
        mark_delivery_dead,
        mark_delivery_failed,
        reap_expired_webhook_leases,
        worker_identity,
    )

    worker = worker_identity()
    logger.info("worker.start", extra={"worker": worker, "loop": "delivery"})
    passes = 0

    while not shutdown.requested:
        passes += 1

        if passes % reap_every_n_passes == 0:
            with SessionLocal() as db:
                reaped = reap_expired_webhook_leases(db)
                db.commit()
            if reaped:
                logger.warning("delivery.reaped", extra={"count": reaped})

        work: list[tuple] = []
        with SessionLocal() as db:
            claimed = claim_webhook_deliveries(
                db,
                worker_id=worker,
                batch_size=batch_size,
                lease_seconds=lease_seconds,
            )
            for delivery in claimed:
                endpoint = db.execute(
                    select(WebhookEndpoint).where(
                        WebhookEndpoint.id == delivery.webhook_endpoint_id
                    )
                ).scalar_one_or_none()
                work.append((delivery.id, delivery.attempts, endpoint, delivery))
            db.expunge_all()
            db.commit()

        if not work:
            _idle_sleep(idle_sleep_seconds)
            continue

        if shutdown.requested:
            logger.info("delivery.draining", extra={"remaining": len(work)})

        for delivery_id, attempt_number, endpoint, delivery in work:
            with system_principal(job_name="webhook.delivery", job_id=delivery_id):
                if endpoint is None:
                    with SessionLocal() as db:
                        mark_delivery_dead(
                            db, delivery_id, error="Endpoint no longer exists."
                        )
                        db.commit()
                    continue
                if endpoint.status is not WebhookEndpointStatus.ACTIVE:
                    with SessionLocal() as db:
                        mark_delivery_dead(
                            db,
                            delivery_id,
                            error=(
                                "Endpoint is DISABLED: "
                                f"{endpoint.disabled_reason or 'no reason recorded'}"
                            ),
                        )
                        db.commit()
                    continue

                try:
                    outcome = attempt_delivery(
                        endpoint, delivery, attempt_number=attempt_number
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "delivery.dispatch_crashed",
                        extra={"webhook_delivery_id": str(delivery_id)},
                    )
                    with SessionLocal() as db:
                        mark_delivery_failed(
                            db,
                            delivery_id,
                            attempts=attempt_number,
                            error=f"DISPATCH_BUG: {type(exc).__name__}: {exc}",
                        )
                        db.commit()
                    continue

                with SessionLocal() as db:
                    persisted = db.execute(
                        select(WebhookDelivery).where(
                            WebhookDelivery.id == delivery_id
                        )
                    ).scalar_one()
                    record_outcome(db, persisted, outcome)
                    db.commit()

    logger.info(
        "worker.stopped", extra={"worker": worker, "loop": "delivery", "passes": passes}
    )


# ======================================================================
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="app.worker")
    parser.add_argument(
        "--loop",
        choices=["relay", "delivery"],
        required=True,
        help="relay: outbox -> deliveries. delivery: deliveries -> network.",
    )
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument(
        "--lease-seconds",
        type=int,
        default=None,
        help="defaults to 60 (relay) / 120 (delivery).",
    )
    parser.add_argument("--idle-sleep", type=float, default=1.0)
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"]
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    assert_no_heavy_imports()

    lease = args.lease_seconds
    if lease is None:
        lease = 60 if args.loop == "relay" else 120

    shutdown = GracefulShutdown().install()
    runner = run_relay_loop if args.loop == "relay" else run_delivery_loop
    try:
        runner(
            shutdown=shutdown,
            batch_size=args.batch_size,
            lease_seconds=lease,
            idle_sleep_seconds=args.idle_sleep,
        )
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())