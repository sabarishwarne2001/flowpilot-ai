"""HARDENING-T1:D25 — a document whose job died must say so.

Before this module, `worker.py` marked an exhausted job DEAD and did nothing
else. A document whose `document.extract` or `document.enrich` job died —
repeated OCR errors, storage errors, a worker killed for memory mid-page —
stayed in EXTRACTING or ENRICHING forever: a permanent spinner, no
`document.failed` event, no "Document failed" automation trigger.
ARCH-10 created `ix_work_items_stage_stuck` for exactly this sweep and nothing
ever queried it.

Two mechanisms, because each covers what the other cannot:

* `on_job_dead` runs in the worker the moment a document job dies. It is
  precise and immediate.
* `sweep_stuck_documents` (job `pipeline.sweep_stuck`, every ten minutes)
  catches what the hook cannot see: a worker process killed outright, a job
  row deleted, a stage left behind by a crash between two commits. It only
  fails a document that has been in a working stage longer than
  PIPELINE_STUCK_AFTER_MINUTES AND has no live job, so a slow OCR run is never
  failed underneath a worker that is still on it.

The failure reason starts with a stable code (`PROCESSING_FAILED:`,
`PROCESSING_STALLED:`), and the code is also placed in the `document.failed`
event payload, so rules and webhooks can branch on it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, select

logger = logging.getLogger("app.workers.dead_letter")

#: Jobs whose death means the document cannot finish processing.
DOCUMENT_JOB_TYPES: frozenset[str] = frozenset({"document.extract", "document.enrich"})

#: Stages a document occupies while work is in flight.
WORKING_STAGES: tuple[str, ...] = ("QUEUED", "EXTRACTING", "EXTRACTED", "ENRICHING")

SWEEP_JOB_TYPE = "pipeline.sweep_stuck"
SWEEP_BATCH = 200
_MAX_REASON = 900


def _organization_of(db: Any, work_item_id: uuid.UUID) -> Optional[uuid.UUID]:
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace

    return db.execute(
        select(Workspace.organization_id)
        .join(WorkItem, WorkItem.workspace_id == Workspace.id)
        .where(WorkItem.id == work_item_id)
    ).scalar_one_or_none()


def fail_document(
    db: Any,
    *,
    work_item_id: uuid.UUID,
    code: str,
    detail: str,
) -> bool:
    """Move a document in a working stage to FAILED. True if it moved."""
    from app.models.work_item import WorkItem
    from app.services.pipeline_state import (
        IllegalTransitionError,
        PipelineStage,
        transition_by_id,
    )

    stage = db.execute(
        select(WorkItem.pipeline_stage).where(WorkItem.id == work_item_id)
    ).scalar_one_or_none()
    stage_value = getattr(stage, "value", stage)
    if stage_value not in WORKING_STAGES:
        return False
    organization_id = _organization_of(db, work_item_id)
    if organization_id is None:
        return False
    reason = f"{code}: {detail}"[:_MAX_REASON]
    try:
        transition_by_id(
            db,
            work_item_id=work_item_id,
            to_stage=PipelineStage.FAILED,
            organization_id=organization_id,
            failure_reason=reason,
            event_payload={"error_code": code},
        )
    except IllegalTransitionError:
        # Another writer moved it first (a retry succeeded, a user
        # reprocessed it). Their state wins.
        return False
    return True


def on_job_dead(*, job_type: str, payload: Optional[dict[str, Any]], error: str) -> None:
    """Called by the worker after it marks a job DEAD. Never raises."""
    if job_type not in DOCUMENT_JOB_TYPES:
        return
    raw = (payload or {}).get("work_item_id")
    if not raw:
        return
    try:
        work_item_id = uuid.UUID(str(raw))
    except ValueError:
        return

    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            moved = fail_document(
                db,
                work_item_id=work_item_id,
                code="PROCESSING_FAILED",
                detail=f"{job_type} gave up after its final attempt ({error})",
            )
            db.commit()
        if moved:
            logger.warning(
                "pipeline.document_failed_on_dead_job",
                extra={"work_item_id": str(work_item_id), "job_type": job_type},
            )
    except Exception:  # noqa: BLE001 - the sweep is the backstop
        logger.exception(
            "pipeline.dead_letter_hook_failed",
            extra={"work_item_id": str(work_item_id), "job_type": job_type},
        )


def sweep_stuck_documents(
    db: Any, *, now: Optional[datetime] = None, stuck_after: Optional[timedelta] = None
) -> dict[str, Any]:
    """Fail documents stranded in a working stage with no live job."""
    from app.core.config import settings
    from app.models.job import Job, JobStatus
    from app.models.work_item import WorkItem

    now = now or datetime.now(timezone.utc)
    stuck_after = stuck_after or timedelta(
        minutes=int(getattr(settings, "PIPELINE_STUCK_AFTER_MINUTES", 90))
    )
    cutoff = now - stuck_after

    # Served by ix_work_items_stage_stuck (partial on the working stages).
    candidates = db.execute(
        select(WorkItem.id)
        .where(
            WorkItem.pipeline_stage.in_(WORKING_STAGES),
            WorkItem.stage_updated_at.is_not(None),
            WorkItem.stage_updated_at < cutoff,
        )
        .order_by(WorkItem.stage_updated_at.asc())
        .limit(SWEEP_BATCH)
    ).scalars().all()

    failed: list[str] = []
    skipped_live: list[str] = []
    for work_item_id in candidates:
        live = db.execute(
            select(Job.id)
            .where(
                and_(
                    Job.job_type.in_(tuple(DOCUMENT_JOB_TYPES)),
                    Job.status.in_(
                        (JobStatus.PENDING, JobStatus.CLAIMED, JobStatus.FAILED)
                    ),
                    Job.payload["work_item_id"].astext == str(work_item_id),
                )
            )
            .limit(1)
        ).scalar_one_or_none()
        if live is not None:
            skipped_live.append(str(work_item_id))
            continue
        if fail_document(
            db,
            work_item_id=work_item_id,
            code="PROCESSING_STALLED",
            detail=(
                "no processing job has been active for this document for over "
                f"{int(stuck_after.total_seconds() // 60)} minutes; reprocess it to try again"
            ),
        ):
            failed.append(str(work_item_id))
        db.commit()

    if failed:
        logger.warning("pipeline.stuck_documents_failed", extra={"count": len(failed)})
    return {
        "candidates": len(candidates),
        "failed": len(failed),
        "skipped_live_job": len(skipped_live),
        "failed_ids": failed[:50],
    }


def handle_pipeline_sweep_stuck(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        return sweep_stuck_documents(db)


__all__ = [
    "DOCUMENT_JOB_TYPES",
    "SWEEP_JOB_TYPE",
    "WORKING_STAGES",
    "fail_document",
    "handle_pipeline_sweep_stuck",
    "on_job_dead",
    "sweep_stuck_documents",
]
