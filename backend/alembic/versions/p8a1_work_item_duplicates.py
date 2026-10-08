"""F-158 — an upload that repeats a document already in the workspace is flagged.

Revision ID: p8a1_work_item_duplicates
Revises: p7a1_automation_rule_soft_delete
Create Date: 2026-10-08

`work_items.duplicate_of_work_item_id`: the earliest document in the same workspace whose file has
the same SHA-256, recorded at upload when the workspace's "Duplicate detection" setting is on (it
always defaulted on and nothing read it). Additive and nullable; the reference is cleared if the
original is deleted. Downgrade drops the column and its index.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "p8a1_work_item_duplicates"
down_revision = "p7a1_automation_rule_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_items",
        sa.Column("duplicate_of_work_item_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_work_items_duplicate_of_work_item_id_work_items",
        "work_items",
        "work_items",
        ["duplicate_of_work_item_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_work_items_duplicate_of",
        "work_items",
        ["duplicate_of_work_item_id"],
        postgresql_where=sa.text("duplicate_of_work_item_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_work_items_duplicate_of", table_name="work_items")
    op.drop_constraint("fk_work_items_duplicate_of_work_item_id_work_items", "work_items", type_="foreignkey")
    op.drop_column("work_items", "duplicate_of_work_item_id")
