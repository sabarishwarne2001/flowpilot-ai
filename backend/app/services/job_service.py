"""ARCH-09 Step 10b — job enqueue, handler registry, and mark functions."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus

logger = logging.getLogger(__name__)


class JobServiceError(Exception):
    pass


class UnknownJobTypeError(JobServiceError):
    pass


def _noop_test_handler(payload: dict[str, Any]) -> dict[str, Any]:
    return {"echo": payload}


def _failing_test_handler(payload: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError(f"intentional test failure: {payload.get('reason', 'none given')}")


JOB_HANDLERS: dict[str, Callable[[dict[str, Any]], Optional[dict[str, Any]]]] = {
    "test.noop": _noop_test_handler,
    "test.always_fails": _failing_test_handler,
}


def register_handler(
    job_type: str, handler: Callable[[dict[str, Any]], Optional[dict[str, Any]]]
) -> None:
    if job_type in JOB_HANDLERS:
        raise JobServiceError(f"job_type {job_type!r} is already registered.")
    JOB_HANDLERS[job_type] = handler


def enqueue(
    db: Session,
    *,
    job_type: str,
    payload: Optional[dict[str, Any]] = None,
    organization_id: Optional[uuid.UUID] = None,
    max_attempts: int = 5,
    available_at: Optional[datetime] = None,
    idempotency_key: Optional[str] = None,
    require_active_transaction: bool = True,
) -> Job:
    if job_type not in JOB_HANDLERS:
        raise UnknownJobTypeError(
            f"'{job_type}' has no registered handler. Known: "
            f"{sorted(JOB_HANDLERS)}. Register one via "
            "app.services.job_service.register_handler() before enqueueing."
        )
    if max_attempts < 1:
        raise JobServiceError("max_attempts must be >= 1.")

    if require_active_transaction and not db.in_transaction():
        raise JobServiceError(
            "enqueue() was called outside an active transaction."
        )

    job = Job(
        job_type=job_type,
        payload=payload or {},
        organization_id=organization_id,
        max_attempts=max_attempts,
        idempotency_key=idempotency_key,
        status=JobStatus.PENDING,
    )
    if available_at is not None:
        job.available_at = available_at

    db.add(job)
    db.flush()
    logger.info(
        "jobs.enqueue",
        extra={
            "job_id": str(job.id),
            "seq": job.seq,
            "job_type": job_type,
            "organization_id": str(organization_id) if organization_id else None,
        },
    )
    return job