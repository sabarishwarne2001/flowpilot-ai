"""ARCH-09 Step 6b, 9, 10, ARCH-10 Step 8, ARCH-15 Step 15.2, ARCH-17 — the worker entrypoint."""

from __future__ import annotations

import argparse
import logging
import random
import signal
import sys
import time
from types import FrameType
from typing import Optional, Sequence

from app.core.config import settings
from app.workers.handlers import register_all
from app.workers.profiles import (
    assert_imports_match_profile,
    claimable_job_types,
    get_profile,
)

logger = logging.getLogger("app.worker")


class GracefulShutdown:
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


def run_relay_loop(
    *,
    shutdown: GracefulShutdown,
    batch_size: int,
    lease_seconds: int,
    idle_sleep_seconds: float,
    reap_every_n_passes: int = 20,
    per_org_cap: Optional[int] = None,
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
                per_org_cap=per_org_cap,
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


def run_delivery_loop(
    *,
    shutdown: GracefulShutdown,
    batch_size: int,
    lease_seconds: int,
    idle_sleep_seconds: float,
    reap_every_n_passes: int = 20,
    per_org_cap: Optional[int] = None,
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
                per_org_cap=per_org_cap,
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


def run_jobs_loop(
    *,
    shutdown: GracefulShutdown,
    batch_size: int,
    lease_seconds: int,
    idle_sleep_seconds: float,
    reap_every_n_passes: int = 20,
    job_types: Optional[Sequence[str]] = None,
) -> None:
    from app.core.principal import system_principal
    from app.core import slo_recorder
    from app.core.request_context import job_scope
    from app.db.session import SessionLocal
    from app.services.job_service import JOB_HANDLERS, trace_context_from
    from app.workers.claim import (
        claim_jobs,
        mark_job_dead,
        mark_job_failed,
        mark_job_succeeded,
        reap_expired_job_leases,
        worker_identity,
    )

    worker = worker_identity()
    logger.info(
        "worker.start",
        extra={"worker": worker, "loop": "jobs", "job_types": list(job_types) if job_types else "*"},
    )
    passes = 0

    while not shutdown.requested:
        passes += 1

        if passes % reap_every_n_passes == 0:
            with SessionLocal() as db:
                reaped = reap_expired_job_leases(db)
                slo_recorder.flush(db)
                db.commit()
            if reaped:
                logger.warning("jobs.reaped", extra={"count": reaped})

        with SessionLocal() as db:
            claimed = claim_jobs(
                db,
                worker_id=worker,
                batch_size=batch_size,
                lease_seconds=lease_seconds,
                job_types=job_types,
            )
            snapshot = [
                (j.id, j.job_type, j.payload, j.attempts, j.max_attempts, j.organization_id)
                for j in claimed
            ]
            db.commit()

        if not snapshot:
            _idle_sleep(idle_sleep_seconds)
            continue

        if shutdown.requested:
            logger.info("jobs.draining", extra={"remaining": len(snapshot)})

        for job_id, job_type, payload, attempts, max_attempts, org_id in snapshot:
            with job_scope(
                job_id=job_id,
                job_type=job_type,
                context=trace_context_from(payload),
            ), system_principal(job_name=f"jobs.{job_type}", job_id=job_id):
                handler = JOB_HANDLERS.get(job_type)
                if handler is None:
                    with SessionLocal() as db:
                        mark_job_dead(
                            db,
                            job_id,
                            error=f"UNKNOWN_JOB_TYPE: no handler registered for {job_type!r}",
                        )
                        db.commit()
                    continue

                try:
                    payload_dict = dict(payload or {})
                    payload_dict["job_id"] = str(job_id)
                    result = handler(payload_dict)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "jobs.handler_failed",
                        extra={"job_id": str(job_id), "job_type": job_type},
                    )
                    with SessionLocal() as db:
                        if attempts >= max_attempts:
                            mark_job_dead(db, job_id, error=f"{type(exc).__name__}: {exc}")
                        else:
                            mark_job_failed(
                                db,
                                job_id,
                                attempts=attempts,
                                error=f"{type(exc).__name__}: {exc}",
                            )
                        db.commit()
                    continue

                with SessionLocal() as db:
                    mark_job_succeeded(db, job_id, result=result)
                    db.commit()

    logger.info("worker.stopped", extra={"worker": worker, "loop": "jobs", "passes": passes})


def run_stripe_inbound_loop(
    *,
    shutdown: GracefulShutdown,
    batch_size: int,
    lease_seconds: int,
    idle_sleep_seconds: float,
    reap_every_n_passes: int = 20,
) -> None:
    from app.db.session import SessionLocal
    from app.services.billing import inbound_service
    from app.workers.claim import worker_identity
    from app.workers.handlers.billing import _reconcile_claimed_row

    worker = worker_identity()
    logger.info("worker.start", extra={"worker": worker, "loop": "stripe_inbound"})
    passes = 0

    while not shutdown.requested:
        passes += 1

        if passes % reap_every_n_passes == 0:
            with SessionLocal() as db:
                reaped = inbound_service.reap_expired_leases(db)
                db.commit()
            if reaped:
                logger.warning("stripe_inbound.reaped", extra={"count": reaped})

        with SessionLocal() as db:
            claimed = inbound_service.claim_batch(
                db,
                worker_id=worker,
                batch_size=batch_size,
                lease_seconds=lease_seconds,
            )
            snapshot = [
                (row.id, row.attempts, row.max_attempts) for row in claimed
            ]
            db.commit()

        if not snapshot:
            _idle_sleep(idle_sleep_seconds)
            continue

        if shutdown.requested:
            logger.info("stripe_inbound.draining", extra={"remaining": len(snapshot)})

        for event_id, attempts, max_attempts in snapshot:
            _reconcile_claimed_row(
                event_id, attempts=attempts, max_attempts=max_attempts
            )

    logger.info(
        "worker.stopped",
        extra={"worker": worker, "loop": "stripe_inbound", "passes": passes},
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="app.worker")
    parser.add_argument(
        "--loop",
        choices=["relay", "delivery", "jobs", "stripe"],
        required=True,
        help="relay | delivery | jobs | stripe",
    )
    parser.add_argument("--profile", default=None, help="light | ocr | enrich | all")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--lease-seconds", type=int, default=None)
    parser.add_argument("--idle-sleep", type=float, default=1.0)
    parser.add_argument("--per-org-cap", type=int, default=None)
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"]
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    register_all()
    profile = get_profile(args.profile)
    assert_imports_match_profile(profile)

    if args.per_org_cap is not None and args.per_org_cap >= args.batch_size:
        logger.warning(
            "worker.per_org_cap_ineffective",
            extra={"per_org_cap": args.per_org_cap, "batch_size": args.batch_size},
        )

    lease = args.lease_seconds
    if lease is None:
        if args.loop == "delivery":
            lease = 120
        elif args.loop == "stripe":
            lease = int(getattr(settings, "STRIPE_INBOUND_LEASE_SECONDS", 60))
        else:
            lease = 60

    shutdown = GracefulShutdown().install()
    kwargs = dict(
        shutdown=shutdown,
        batch_size=args.batch_size,
        lease_seconds=lease,
        idle_sleep_seconds=args.idle_sleep,
    )
    if args.loop == "relay":
        runner, extra = run_relay_loop, {"per_org_cap": args.per_org_cap}
    elif args.loop == "delivery":
        runner, extra = run_delivery_loop, {"per_org_cap": args.per_org_cap}
    elif args.loop == "stripe":
        runner, extra = run_stripe_inbound_loop, {}
    else:
        runner, extra = run_jobs_loop, {"job_types": claimable_job_types(profile)}

    try:
        runner(**kwargs, **extra)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
