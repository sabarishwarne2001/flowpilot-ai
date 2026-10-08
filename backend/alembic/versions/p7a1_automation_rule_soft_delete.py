"""F-150 — deleting an automation rule keeps its row (soft delete).

Revision ID: p7a1_automation_rule_soft_delete
Revises: p6a3_user_mfa_factors
Create Date: 2026-10-07

`automation_rules.deleted_at`: set when a rule is deleted. Its executions reference the rule
with ON DELETE RESTRICT (Run history, per-rule spend), so a hard delete of a rule that had ever
run failed with a foreign-key violation. Additive and nullable; downgrade drops the column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "p7a1_automation_rule_soft_delete"
down_revision = "p6a3_user_mfa_factors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "automation_rules",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("automation_rules", "deleted_at")
