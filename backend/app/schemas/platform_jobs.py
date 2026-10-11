"""Campaign session 2: the operator's Background jobs console (app/api/v1/admin/jobs.py)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class JobTypeSummary(BaseModel):
    job_type: str
    pending: int
    running: int
    retrying: int
    dead: int
    succeeded_24h: int
    oldest_waiting_seconds: Optional[float] = None


class JobsTotals(BaseModel):
    pending: int
    running: int
    retrying: int
    dead: int
    expired_leases: int


class JobsSummary(BaseModel):
    generated_at: datetime
    totals: JobsTotals
    by_type: list[JobTypeSummary]


class DeadJob(BaseModel):
    id: uuid.UUID
    job_type: str
    organization_id: Optional[uuid.UUID] = None
    organization_name: Optional[str] = None
    work_item_id: Optional[str] = None
    attempts: int
    max_attempts: int
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DeadJobList(BaseModel):
    items: list[DeadJob]
    total: int


class JobRetryResult(BaseModel):
    id: uuid.UUID
    status: str
