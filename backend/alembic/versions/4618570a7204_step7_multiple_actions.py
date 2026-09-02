"""step7_multiple_actions

Revision ID: 4618570a7204
Revises: bd55b1f7c43c
Create Date: 2026-07-31 10:15:20.977090

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4618570a7204'
down_revision: Union[str, None] = 'bd55b1f7c43c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automation_rules",
        sa.Column(
            "actions",
            sa.JSON(),
            nullable=True,
            server_default="[]"
        )
    )

    op.execute(
        """
        UPDATE automation_rules
        SET actions = json_build_array(
            json_build_object(
                'action_type', action_type,
                'config', action_config
            )
        )
        """
    )

    op.alter_column(
        "automation_rules",
        "actions",
        nullable=False,
        server_default=None
    )

    op.drop_column("automation_rules", "action_config")
    op.drop_column("automation_rules", "action_type")


def downgrade() -> None:
    op.add_column(
        "automation_rules",
        sa.Column("action_type", sa.String(length=50), nullable=False)
    )

    op.add_column(
        "automation_rules",
        sa.Column("action_config", postgresql.JSON(astext_type=sa.Text()), nullable=False)
    )

    op.drop_column("automation_rules", "actions")
