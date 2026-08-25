"""ARCH-16 Step 7 — jobs.effects_suppressed.

Revision ID: arch16_step7_job_suppression
Revises: arch16_step6_assertions_and_replay
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

TBL_JOBS = "jobs"

revision = "arch16_step7_job_suppression"
down_revision = "arch16_step6_assertions_and_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add created_by_user_id if not present
    op.add_column(TBL_JOBS, sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_jobs_created_by_user_id", TBL_JOBS, "users",
        ["created_by_user_id"], ["id"], ondelete="SET NULL")

    # 2. Add suppression columns
    op.add_column(TBL_JOBS, sa.Column("effects_suppressed", sa.Boolean(),
                                      nullable=False, server_default=sa.false()))
    op.add_column(TBL_JOBS, sa.Column("suppressed_at", sa.DateTime(timezone=True)))
    op.add_column(TBL_JOBS, sa.Column("suppressed_reason", sa.Text()))

    op.create_check_constraint(
        "ck_jobs_suppressed_has_timestamp", TBL_JOBS,
        "effects_suppressed = false OR suppressed_at IS NOT NULL")

    # Drives deprovision sweep and release-gate detector using real job_status values
    op.create_index("ix_jobs_principal_live", TBL_JOBS, ["created_by_user_id"],
                    postgresql_where=sa.text("status IN ('PENDING'::job_status, 'CLAIMED'::job_status)"))


def downgrade() -> None:
    op.drop_index("ix_jobs_principal_live", table_name=TBL_JOBS)
    op.drop_constraint("ck_jobs_suppressed_has_timestamp", TBL_JOBS, type_="check")
    op.drop_constraint("fk_jobs_created_by_user_id", TBL_JOBS, type_="foreignkey")
    for col in ("suppressed_reason", "suppressed_at", "effects_suppressed", "created_by_user_id"):
        op.drop_column(TBL_JOBS, col)