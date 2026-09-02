"""ARCH-10 Step 8 — job-type routing index (EXPAND)

Revision ID: arch10_step8_jobs_type_index
Revises: arch10_step7_contract
Create Date: 2026-08-18
"""

from __future__ import annotations

from alembic import op

revision = "arch10_step8_jobs_type_index"
down_revision = "arch10_step7_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_claimable_by_type "
            "ON jobs (job_type, available_at, seq) "
            "WHERE status IN ('PENDING'::job_status, 'FAILED'::job_status)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_claimable_by_type")
