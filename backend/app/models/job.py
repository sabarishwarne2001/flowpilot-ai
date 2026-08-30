"""
Database representation for Job entities in FlowPilot AI.
Generic system job queue for offloading async tasks (ARCH-09 §Step 10, ARCH-16 §Step 7).
"""

from __future__ import annotations

import enum
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

logger = logging.getLogger(__name__)


class JobStatus(str, enum.Enum):
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
        CheckConstraint(
            "effects_suppressed = false OR suppressed_at IS NOT NULL",
            name="ck_jobs_suppressed_has_timestamp",
        ),
        CheckConstraint(
            "trace_id IS NULL OR trace_id ~ '^[0-9a-f]{32}$'",
            name="ck_jobs_trace_id_is_w3c_hex",
        ),
        UniqueConstraint("seq", name="uq_jobs_seq"),
        Index(
            "ix_jobs_claimable",
            "available_at",
            "seq",
            postgresql_where=text("status IN ('PENDING'::job_status, 'FAILED'::job_status)"),
        ),
        Index(
            "ix_jobs_claimable_by_type",
            "job_type",
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
        Index(
            "ix_jobs_trace_id",
            "trace_id",
            postgresql_where=text("trace_id IS NOT NULL"),
        ),
        Index(
            "ix_jobs_correlation_id",
            "correlation_id",
            postgresql_where=text("correlation_id IS NOT NULL"),
        ),
        Index(
            "ix_jobs_principal_live",
            "created_by_user_id",
            postgresql_where=text("status IN ('PENDING'::job_status, 'CLAIMED'::job_status)"),
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
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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

    trace_id: Mapped[Optional[str]] = mapped_column(
        String(32),
        nullable=True,
    )
    correlation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    effects_suppressed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    suppressed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suppressed_reason: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES

    def __repr__(self) -> str:
        return (
            f"<Job seq={self.seq} type={self.job_type} "
            f"status={self.status.value if self.status else None}>"
        )