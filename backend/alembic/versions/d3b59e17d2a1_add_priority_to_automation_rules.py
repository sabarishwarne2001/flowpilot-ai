"""add priority to automation rules

Revision ID: d3b59e17d2a1
Revises: 1f85effe07bb
Create Date: 2026-07-30 13:03:53.317058

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3b59e17d2a1'
down_revision: Union[str, None] = '1f85effe07bb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automation_rules",
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="100",
        ),
    )

    op.create_index(
        op.f("ix_automation_rules_priority"),
        "automation_rules",
        ["priority"],
        unique=False,
    )

    op.alter_column(
        "automation_rules",
        "priority",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_automation_rules_priority"),
        table_name="automation_rules",
    )

    op.drop_column(
        "automation_rules",
        "priority",
    )
