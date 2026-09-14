"""ARCH-32 — the `redaction.detect` and `redaction.apply` jobs.

WHY BOTH ARE ON THE OCR PROFILE AND NOT ON LIGHT
================================================

`redaction.apply` renders every page at 300 DPI through PDFium and holds a
full-page uint8 array in memory while it burns. That is the same class of work
`document.extract` does, and it is exactly what the OCR image was built to
carry. Putting it on LIGHT would let a forty-page scan exhaust a thin worker
that also serves notification delivery and billing reconciliation.

`redaction.detect` is cheaper — it reads text that is already extracted — but
it still calls `rasterize.page_dimensions`, which opens the PDF, and §3.3
reserves the option of re-running PaddleOCR geometry for pages the text layer
did not cover. Splitting the two across profiles would mean a detect that
cannot grow into that without a migration of the queue.

THE THREE PLACES
================

Both job types must appear in all three, and the fleet refuses to boot if they
do not:

  1. `app/workers/handlers/__init__.py`  — the handler map
  2. `app/workers/profiles.py` OCR       — the profile that may claim them
  3. `app/workers/scheduler.py`          — only for recurring work

Neither of these is recurring, so neither goes in the scheduler. That is a
decision, not an omission: a sweep for "jobs stuck in APPLYING" would be a
reasonable future addition, and it would be a scheduled job with its own
handler rather than a periodic re-run of this one. Re-running an apply on a
schedule would re-render and re-upload a document somebody may have cancelled.

`assert_imports_match_profile()` runs `uncovered_job_types()` at EVERY
worker's startup and raises `ProfileError` on a handler no profile claims.
Registering here without adding to `profiles.py` stops the entire fleet
booting — the defect ARCH-16 shipped, which the comments in `profiles.py`
memorialise.

ONE JOB'S FAILURE IS ITS OWN
============================

`run_apply` catches its own exceptions and writes FAILED with a reason, so a
document that PDFium cannot render does not surface here as a raised
exception the supervisor would retry five times — five renders of a file that
will never render. What DOES raise here is a job row that has vanished or a
payload that is malformed, because those are bugs in the enqueue path and
should be loud.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.db.session import SessionLocal
from app.services.redaction import redaction_service

logger = logging.getLogger("app.workers.handlers.redaction")

DETECT_JOB_TYPE = redaction_service.DETECT_JOB_TYPE
APPLY_JOB_TYPE = redaction_service.APPLY_JOB_TYPE

__all__ = [
    "DETECT_JOB_TYPE",
    "APPLY_JOB_TYPE",
    "handle_redaction_detect",
    "handle_redaction_apply",
]


def _job_id(payload: dict[str, Any]) -> uuid.UUID:
    raw = payload.get("redaction_job_id")
    if not raw:
        raise ValueError(
            "redaction job payload is missing redaction_job_id. This is an "
            "enqueue-side bug; retrying cannot fix it."
        )
    return uuid.UUID(str(raw))


def handle_redaction_detect(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _job_id(payload)
    with SessionLocal() as db:
        try:
            result = redaction_service.run_detection(db, job_id=job_id)
            db.commit()
        except Exception:
            db.rollback()
            # Detection failing leaves the job in DETECTING, which the studio
            # renders as "still working". Marking it FAILED needs its own
            # transaction, because the one that raised is poisoned.
            with SessionLocal() as recovery:
                _mark_failed(
                    recovery,
                    job_id,
                    "Detection could not complete. No regions were written.",
                )
                recovery.commit()
            raise
    logger.info("redaction.detect_complete", extra={"redaction_job_id": str(job_id), **result})
    return result


def handle_redaction_apply(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _job_id(payload)
    with SessionLocal() as db:
        result = redaction_service.run_apply(db, job_id=job_id)
        db.commit()
    logger.info("redaction.apply_complete", extra={"redaction_job_id": str(job_id), **result})
    return result


def _mark_failed(db: Any, job_id: uuid.UUID, reason: str) -> None:
    from app.models.redaction import JOB_STATUS_FAILED, RedactionJob

    job = db.get(RedactionJob, job_id)
    if job is None:
        return
    job.status = JOB_STATUS_FAILED
    job.failure_reason = reason
    db.flush([job])