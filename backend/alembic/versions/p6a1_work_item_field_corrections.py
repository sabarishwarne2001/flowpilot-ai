"""FINAL RELEASE (N-020 item 7) — corrections made in the document viewer.

Revision ID: p6a1_work_item_field_corrections
Revises: p5a1_schema_drift_alignment
Create Date: 2026-10-06

WHAT CHANGES
============

1. `audit_resource_type` gains WORK_ITEM: a correction of an extracted value is
   audited against the document it changed. Added in an autocommit block,
   because PostgreSQL refuses to use an enum value in the transaction that
   added it; nothing in this migration uses it.

2. `work_item_field_corrections`: one row per corrected field, with the value
   before and after, who corrected it and why. Rows cascade with the document
   and the workspace; the corrector is SET NULL, so a person's corrections
   outlive their account like their uploads do (F-103).

Additive only. Downgrade drops the table; the enum value stays, as every
earlier vocabulary migration explains (PostgreSQL cannot drop one, and the
audit table's immutability trigger forbids rewriting it).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "p6a1_work_item_field_corrections"
down_revision = "p5a1_schema_drift_alignment"
branch_labels = None
depends_on = None

TABLE = "work_item_field_corrections"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS 'WORK_ITEM'")

    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_path", sa.String(length=200), nullable=False),
        sa.Column("previous_value", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("corrected_value", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("corrected_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_work_item_field_corrections"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"],
            name="fk_work_item_field_corrections_workspace_id_workspaces", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"],
            name="fk_work_item_field_corrections_work_item_id_work_items", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["corrected_by_user_id"], ["users.id"],
            name="fk_work_item_field_corrections_corrected_by_user_id_users", ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_work_item_field_corrections_work_item_created",
        TABLE,
        ["work_item_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_work_item_field_corrections_work_item_created", table_name=TABLE)
    op.drop_table(TABLE)
