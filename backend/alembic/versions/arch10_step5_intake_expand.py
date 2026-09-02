"""ARCH-10 Step 5 — work_items intake linkage (EXPAND)

Revision ID: arch10_step5_intake_expand
Revises: arch10_step3_spend_limits
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch10_step5_intake_expand"
down_revision = "arch10_step3_spend_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_items",
        sa.Column("uploaded_file_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("work_items", sa.Column("page_count", sa.Integer(), nullable=True))
    op.add_column("work_items", sa.Column("extracted_text", sa.Text(), nullable=True))
    op.add_column(
        "work_items",
        sa.Column(
            "extraction_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_work_items_uploaded_file_id_uploaded_files",
        "work_items",
        "uploaded_files",
        ["uploaded_file_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(
        "CREATE UNIQUE INDEX uq_work_items_uploaded_file_id "
        "ON work_items (uploaded_file_id) WHERE uploaded_file_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_work_items_page_count "
        "ON work_items (page_count) WHERE page_count IS NOT NULL"
    )

    op.create_check_constraint(
        "ck_work_items_page_count_positive",
        "work_items",
        "page_count IS NULL OR page_count > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_work_items_page_count_positive", "work_items", type_="check"
    )
    op.execute("DROP INDEX IF EXISTS ix_work_items_page_count")
    op.execute("DROP INDEX IF EXISTS uq_work_items_uploaded_file_id")
    op.drop_constraint(
        "fk_work_items_uploaded_file_id_uploaded_files",
        "work_items",
        type_="foreignkey",
    )
    op.drop_column("work_items", "extraction_metadata")
    op.drop_column("work_items", "extracted_text")
    op.drop_column("work_items", "page_count")
    op.drop_column("work_items", "uploaded_file_id")
