"""Campaign session 2: the operator's Background jobs console (platform superadmins).

    GET  /admin/jobs/summary          waiting, running, retrying and dead jobs per type
    GET  /admin/jobs/dead             the dead-letter queue: jobs that failed every attempt
    POST /admin/jobs/{id}/retry       send a dead job round again once its cause is fixed

Every route is cross-tenant, so the superadmin dependency is declared once on the router
(404 for anyone else), as the other platform consoles do. Before this a dead job was visible
only as a failed document or a database row.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_read_db, require_superadmin
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.job import Job, JobStatus
from app.models.organization import Organization
from app.models.user import User
from app.schemas.platform_jobs import (
    DeadJob,
    DeadJobList,
    JobRetryResult,
    JobsSummary,
    JobsTotals,
    JobTypeSummary,
)
from app.services import audit_service

logger = logging.getLogger("app.api.admin.jobs")

router = APIRouter(prefix="/admin/jobs", tags=["Platform jobs"], dependencies=[Depends(require_superadmin)])

MAX_DEAD_PAGE = 200


@router.get("/summary", response_model=JobsSummary, summary="Jobs per type and status")
def jobs_summary(db: Session = Depends(get_read_db)) -> JobsSummary:
    now = func.now()
    waiting = Job.status == JobStatus.PENDING
    rows = db.execute(
        select(
            Job.job_type,
            func.count().filter(waiting),
            func.count().filter(Job.status == JobStatus.CLAIMED),
            func.count().filter(Job.status == JobStatus.FAILED),
            func.count().filter(Job.status == JobStatus.DEAD),
            func.count().filter(and_(
                Job.status == JobStatus.SUCCEEDED,
                Job.succeeded_at >= now - text("interval '1 day'"),
            )),
            func.min(case((waiting, Job.available_at), else_=None)),
            func.count().filter(and_(Job.status == JobStatus.CLAIMED, Job.claim_expires_at < now)),
        )
        .where(or_(Job.status != JobStatus.SUCCEEDED, Job.succeeded_at >= now - text("interval '1 day'")))
        .group_by(Job.job_type)
        .order_by(Job.job_type)
    ).all()

    generated_at = datetime.now(timezone.utc)
    by_type: list[JobTypeSummary] = []
    totals = {"pending": 0, "running": 0, "retrying": 0, "dead": 0, "expired_leases": 0}
    for job_type, pending, running, retrying, dead, done_24h, oldest, expired in rows:
        oldest_wait: Optional[float] = None
        if oldest is not None:
            oldest_wait = max(0.0, (generated_at - oldest).total_seconds())
        by_type.append(JobTypeSummary(
            job_type=job_type, pending=pending, running=running, retrying=retrying, dead=dead,
            succeeded_24h=done_24h, oldest_waiting_seconds=oldest_wait,
        ))
        totals["pending"] += pending
        totals["running"] += running
        totals["retrying"] += retrying
        totals["dead"] += dead
        totals["expired_leases"] += expired
    return JobsSummary(generated_at=generated_at, totals=JobsTotals(**totals), by_type=by_type)


@router.get("/dead", response_model=DeadJobList, summary="The dead-letter queue")
def dead_jobs(
    job_type: Optional[str] = Query(None, max_length=128),
    limit: int = Query(50, ge=1, le=MAX_DEAD_PAGE),
    db: Session = Depends(get_read_db),
) -> DeadJobList:
    conditions = [Job.status == JobStatus.DEAD]
    if job_type:
        conditions.append(Job.job_type == job_type)
    total = db.execute(select(func.count()).select_from(Job).where(*conditions)).scalar_one()
    rows = db.execute(
        select(Job, Organization.name)
        .outerjoin(Organization, Organization.id == Job.organization_id)
        .where(*conditions)
        .order_by(Job.updated_at.desc())
        .limit(limit)
    ).all()
    items = []
    for job, organization_name in rows:
        work_item_id = (job.payload or {}).get("work_item_id")
        items.append(DeadJob(
            id=job.id, job_type=job.job_type, organization_id=job.organization_id,
            organization_name=organization_name, work_item_id=str(work_item_id) if work_item_id else None,
            attempts=job.attempts, max_attempts=job.max_attempts, last_error=job.last_error,
            created_at=job.created_at, updated_at=job.updated_at,
        ))
    return DeadJobList(items=items, total=total)


@router.post("/{job_id}/retry", response_model=JobRetryResult, summary="Send a dead job round again")
def retry_dead_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_superadmin),
) -> JobRetryResult:
    row = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.DEAD)
        .values(
            status=JobStatus.PENDING,
            attempts=0,
            available_at=func.now(),
            claimed_at=None,
            claimed_by=None,
            claim_expires_at=None,
            updated_at=func.now(),
        )
        .returning(Job.id, Job.job_type, Job.organization_id, Job.last_error)
        .execution_options(synchronize_session=False)
    ).first()
    if row is None:
        exists = db.execute(select(Job.status).where(Job.id == job_id)).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a dead job can be retried; this one is {exists.value}.")
    details = {"event": "job.retried_from_dead", "job_id": str(row.id), "job_type": row.job_type,
               "last_error": (row.last_error or "")[:300]}
    if row.organization_id is not None:
        # The organization's own trail shows that platform staff re-ran one of its jobs.
        audit_service.record(
            db,
            organization_id=row.organization_id,
            actor_id=current_user.id,
            resource_type=AuditResourceType.ORGANIZATION,
            resource_id=row.organization_id,
            action=AuditAction.UPDATED,
            outcome=AuditOutcome.ALLOWED,
            details=details,
        )
    logger.warning("platform.job_retried", extra={**details, "actor_id": str(current_user.id)})
    db.commit()
    return JobRetryResult(id=row.id, status=JobStatus.PENDING.value)
