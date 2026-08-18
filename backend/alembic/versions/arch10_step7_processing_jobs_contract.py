"""ARCH-10 Step 7 — processing_jobs CONTRACT

Revision ID: arch10_step7_contract
Revises: arch10_step7_pipeline_expand
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "arch10_step7_contract"
down_revision = "arch10_step7_pipeline_expand"
branch_labels = None
depends_on = None

LEGACY_JOB_TYPE = "legacy.processing_job"


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Migrate any remaining legacy rows from processing_jobs into jobs
    bind.execute(
        sa.text(
            f"""
            INSERT INTO jobs (
                id, job_type, payload, result,
                organization_id, idempotency_key,
                status, available_at,
                claimed_at, claimed_by, claim_expires_at,
                attempts, max_attempts, last_error,
                succeeded_at, created_at, updated_at
            )
            SELECT
                pj.id,
                '{LEGACY_JOB_TYPE}',
                jsonb_strip_nulls(jsonb_build_object(
                    'work_item_id',       pj.work_item_id::text,
                    'legacy_status',      pj.status,
                    'legacy_progress',    pj.progress,
                    'legacy_retry_count', pj.retry_count,
                    'execution_metadata', to_jsonb(pj.execution_metadata),
                    'migrated_by',        'arch10_step7_contract'
                )),
                NULL,
                w.organization_id,
                'legacy:processing_job:' || pj.id::text,
                CASE WHEN pj.status = 'COMPLETED'
                     THEN 'SUCCEEDED'::job_status
                     ELSE 'DEAD'::job_status
                END,
                pj.created_at,
                NULL, NULL, NULL,
                COALESCE(pj.retry_count, 0),
                GREATEST(COALESCE(pj.retry_count, 0), 1),
                LEFT(pj.error_message, 4000),
                CASE WHEN pj.status = 'COMPLETED'
                     THEN COALESCE(pj.updated_at, pj.created_at)
                     ELSE NULL
                END,
                pj.created_at,
                COALESCE(pj.updated_at, pj.created_at)
            FROM processing_jobs pj
            LEFT JOIN work_items wi ON wi.id = pj.work_item_id
            LEFT JOIN workspaces  w  ON w.id  = wi.workspace_id
            ON CONFLICT (id) DO NOTHING
            """
        )
    )

    # 2. Verify no unmigrated rows remain in processing_jobs
    unmigrated = bind.execute(
        sa.text(
            f"""
            SELECT count(*) FROM processing_jobs pj
            WHERE NOT EXISTS (
                SELECT 1 FROM jobs j WHERE j.id = pj.id AND j.job_type = '{LEGACY_JOB_TYPE}'
            )
            """
        )
    ).scalar_one()

    if unmigrated > 0:
        raise RuntimeError(
            f"processing_jobs CONTRACT aborted: {unmigrated} rows failed to migrate into jobs."
        )

    # 3. Assert no legacy row was inserted in a claimable state
    claimable = bind.execute(
        sa.text(
            "SELECT count(*) FROM jobs "
            "WHERE job_type = :t AND status IN ('PENDING','FAILED')"
        ),
        {"t": LEGACY_JOB_TYPE},
    ).scalar_one()
    if claimable:
        raise RuntimeError(
            f"{claimable} migrated legacy rows landed in a CLAIMABLE status."
        )

    # 4. Drop the legacy table
    op.drop_table("processing_jobs")


def downgrade() -> None:
    op.create_table(
        "processing_jobs",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="PENDING"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(length=5000), nullable=True),
        sa.Column("execution_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "work_item_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False
        ),
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
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_processing_jobs_status", "processing_jobs", ["status"])
    op.create_index(
        "ix_processing_jobs_work_item_id", "processing_jobs", ["work_item_id"]
    )