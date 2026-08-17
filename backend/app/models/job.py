"""
Database representations for Job entities in FlowPilot AI:
1. ProcessingJob (processing_jobs table) - Discrete execution runs linked to parent Work Items.
2. Job (jobs table) - Generic system job queue for offloading async tasks (ARCH-09 Step 10).
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, Optional, TYPE_CHECKING, Union

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.work_item import WorkItem


# ============================================================================
# 1. ProcessingJob (Work Item execution pipeline runs)
# ============================================================================
class ProcessingJob(Base, UUIDMixin, TimestampMixin):
    """
    Persistent representation of a single background pipeline execution run.
    """
    __tablename__ = "processing_jobs"

    progress: Mapped[int] = mapped_column(
        Integer, 
        default=0, 
        nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default="PENDING",
        index=True,
        nullable=False
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, 
        default=0, 
        nullable=False
    )
    error_message: Mapped[Union[str, None]] = mapped_column(
        String(5000),
        nullable=True
    )
    
    execution_metadata: Mapped[Union[dict[str, Any], None]] = mapped_column(
        JSON, 
        nullable=True
    )

    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )

    work_item: Mapped["WorkItem"] = relationship(
        "WorkItem", 
        back_populates="jobs"
    )


# ============================================================================
# 2. Job (ARCH-09 Step 10 — Generic System Job Queue)
# ============================================================================
class JobStatus(str, PyEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DEAD = "DEAD"


CLAIMABLE_JOB_STATUSES: tuple[JobStatus, ...] = (JobStatus.PENDING, JobStatus.FAILED)
TERMINAL_JOB_STATUSES: tuple[JobStatus, ...] = (JobStatus.SUCCEEDED, JobStatus.DEAD)


class Job(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "jobs"

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint(
            "(status = 'CLAIMED'::job_status) = (claim_expires_at IS NOT NULL)",
            name="lease_matches_status",
        ),
        CheckConstraint(
            "(status = 'SUCCEEDED'::job_status) = (succeeded_at IS NOT NULL)",
            name="succeeded_at_matches_status",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_is_object"),
        UniqueConstraint("seq", name="uq_jobs_seq"),
        Index(
            "ix_jobs_claimable",
            "available_at",
            "seq",
            postgresql_where=text("status IN ('PENDING'::job_status, 'FAILED'::job_status)"),
        ),
        Index(
            "ix_jobs_expired_leases",
            "claim_expires_at",
            postgresql_where=text("status = 'CLAIMED'::job_status"),
        ),
        Index(
            "ix_jobs_organization_id_created_at",
            "organization_id",
            text("created_at DESC"),
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
        Index("ix_jobs_job_type_created_at", "job_type", text("created_at DESC")),
        Index(
            "ix_jobs_prunable",
            "succeeded_at",
            postgresql_where=text("status = 'SUCCEEDED'::job_status"),
        ),
        Index(
            "uq_jobs_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    seq: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False, start=1), nullable=False, unique=True
    )

    job_type: Mapped[str] = mapped_column(String(150), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    status: Mapped[JobStatus] = mapped_column(
        PGEnum(JobStatus, name="job_status", create_type=False, validate_strings=True),
        nullable=False,
        server_default=text("'PENDING'::job_status"),
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    claim_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    succeeded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Job seq={self.seq} type={self.job_type} "
            f"status={self.status.value if self.status else None}>"
        )