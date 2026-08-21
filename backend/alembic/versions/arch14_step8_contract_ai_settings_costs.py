"""ARCH-14 Step 8 — CONTRACT: drop ai_settings cost columns

Revision ID: arch14_step8_contract_ai_settings_costs
Revises: arch14_step5_reconciliation
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "arch14_step8_contract_ai_settings_costs"
down_revision = "arch14_step5_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    residue = connection.execute(
        sa.text(
            """
            SELECT count(*) FROM ai_settings
             WHERE COALESCE(input_cost_per_1k_tokens, 0) <> 0
                OR COALESCE(output_cost_per_1k_tokens, 0) <> 0
            """
        )
    ).scalar_one()
    print(
        f"[arch14_step8] dropping ai_settings cost columns; {residue} rows "
        "held a non-zero customer-supplied price (ignored since ARCH-14.1)"
    )

    op.drop_column("ai_settings", "input_cost_per_1k_tokens")
    op.drop_column("ai_settings", "output_cost_per_1k_tokens")


def downgrade() -> None:
    op.add_column(
        "ai_settings",
        sa.Column(
            "input_cost_per_1k_tokens",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
    )
    op.add_column(
        "ai_settings",
        sa.Column(
            "output_cost_per_1k_tokens",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
    )