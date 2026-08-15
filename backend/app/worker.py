"""ARCH-09 Step 3 — the worker entrypoint."""

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
    leaked = [
        name
        for name in _HEAVY_MODULES
        if name not in allow and name in sys.modules
    ]
    if leaked:
        raise RuntimeError(
            "ARCH-09 §B.8 import isolation violated: "
            f"{', '.join(leaked)} loaded into the delivery worker."
        )


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


def run_outbox_loop(
    *,
    shutdown: GracefulShutdown,
    batch_size: int,
    lease_seconds: int,
    idle_sleep_seconds: float,
    reap_every_n_passes: int = 20,
) -> None:
    from app.core.principal import system_principal
    from app.db.session import SessionLocal
    from app.workers.claim import (
        claim_batch,
        mark_failed,
        mark_published,
        reap_expired_leases,
        worker_identity,
    )

    worker = worker_identity()
    logger.info("worker.start", extra={"worker": worker, "loop": "outbox"})
    passes = 0

    while not shutdown.requested:
        passes += 1

        if passes % reap_every_n_passes == 0:
            with SessionLocal() as db:
                reaped = reap_expired_leases(db)
                db.commit()
            if reaped:
                logger.warning("worker.reaped", extra={"count": reaped})

        with SessionLocal() as db:
            claimed = claim_batch(
                db,
                worker_id=worker,
                batch_size=batch_size,
                lease_seconds=lease_seconds,
            )
            snapshot = [
                (event.id, event.event_type, event.attempts, event.organization_id)
                for event in claimed
            ]
            db.commit()

        if not snapshot:
            time.sleep(idle_sleep_seconds * random.uniform(0.5, 1.5))
            continue

        if shutdown.requested:
            logger.info("worker.draining", extra={"remaining": len(snapshot)})

        for event_id, event_type, attempts, organization_id in snapshot:
            with system_principal(job_name="outbox.relay", job_id=event_id):
                try:
                    _process_one(
                        event_id=event_id,
                        event_type=event_type,
                        organization_id=organization_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "worker.event_failed",
                        extra={
                            "outbox_event_id": str(event_id),
                            "event_type": event_type,
                            "attempts": attempts,
                        },
                    )
                    with SessionLocal() as db:
                        mark_failed(
                            db,
                            event_id,
                            attempts=attempts,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                        db.commit()
                else:
                    with SessionLocal() as db:
                        mark_published(db, event_id)
                        db.commit()

    logger.info("worker.stopped", extra={"worker": worker, "passes": passes})


def _process_one(*, event_id, event_type, organization_id) -> None:
    logger.info(
        "outbox.relay.noop",
        extra={
            "outbox_event_id": str(event_id),
            "event_type": event_type,
            "organization_id": str(organization_id),
        },
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="app.worker")
    parser.add_argument("--loop", choices=["outbox"], default="outbox")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--lease-seconds", type=int, default=120)
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

    shutdown = GracefulShutdown().install()
    try:
        run_outbox_loop(
            shutdown=shutdown,
            batch_size=args.batch_size,
            lease_seconds=args.lease_seconds,
            idle_sleep_seconds=args.idle_sleep,
        )
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())