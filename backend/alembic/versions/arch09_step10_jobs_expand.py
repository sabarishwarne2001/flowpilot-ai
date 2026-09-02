"""ARCH-09 Step 10a — jobs (EXPAND)

Revision ID: arch09_step10_jobs_expand
Revises: arch09_step8_webhook_api_expand
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch09_step10_jobs_expand"
down_revision = "arch09_step8_webhook_api_expand"
branch_labels = None
depends_on = None

JOB_STATUS_VALUES: tuple[str, ...] = ("PENDING", "CLAIMED", "SUCCEEDED", "FAILED", "DEAD")


def upgrade() -> None:
    bind = op.get_bind()

    job_status = postgresql.ENUM(*JOB_STATUS_VALUES, name="job_status")
    job_status.create(bind, checkfirst=True)

    op.create_table(
        "jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "seq",
            sa.BigInteger(),
            sa.Identity(always=False, start=1),
            nullable=False,
        ),
        sa.Column(
            "job_type",
            sa.String(length=150),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=200),
            nullable=True,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(*JOB_STATUS_VALUES, name="job_status", create_type=False),
            nullable=False,
            server_default=sa.text("'PENDING'::job_status"),
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("5"),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        sa.CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        sa.CheckConstraint(
            "(status = 'CLAIMED'::job_status) = (claim_expires_at IS NOT NULL)",
            name="lease_matches_status",
        ),
        sa.CheckConstraint(
            "(status = 'SUCCEEDED'::job_status) = (succeeded_at IS NOT NULL)",
            name="succeeded_at_matches_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="payload_is_object"
        ),
        sa.UniqueConstraint("seq", name="uq_jobs_seq"),
    )

    op.create_index(
        "ix_jobs_claimable",
        "jobs",
        ["available_at", "seq"],
        postgresql_where=sa.text("status IN ('PENDING'::job_status, 'FAILED'::job_status)"),
    )
    op.create_index(
        "ix_jobs_expired_leases",
        "jobs",
        ["claim_expires_at"],
        postgresql_where=sa.text("status = 'CLAIMED'::job_status"),
    )
    op.create_index(
        "ix_jobs_organization_id_created_at",
        "jobs",
        ["organization_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.create_index("ix_jobs_job_type_created_at", "jobs", ["job_type", sa.text("created_at DESC")])
    op.create_index(
        "ix_jobs_prunable",
        "jobs",
        ["succeeded_at"],
        postgresql_where=sa.text("status = 'SUCCEEDED'::job_status"),
    )
    op.create_index(
        "uq_jobs_org_idempotency_key",
        "jobs",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_org_idempotency_key", table_name="jobs")
    op.drop_index("ix_jobs_prunable", table_name="jobs")
    op.drop_index("ix_jobs_job_type_created_at", table_name="jobs")
    op.drop_index("ix_jobs_organization_id_created_at", table_name="jobs")
    op.drop_index("ix_jobs_expired_leases", table_name="jobs")
    op.drop_index("ix_jobs_claimable", table_name="jobs")
    op.drop_table("jobs")
    postgresql.ENUM(name="job_status").drop(op.get_bind(), checkfirst=True)
