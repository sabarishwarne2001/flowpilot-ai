"""multiple_conditions

Revision ID: bd55b1f7c43c
Revises: d3b59e17d2a1
Create Date: 2026-07-30 19:22:58.904946

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text


# revision identifiers, used by Alembic.
revision: str = 'bd55b1f7c43c'
down_revision: Union[str, None] = 'd3b59e17d2a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns with temporary defaults
    op.add_column(
        "automation_rules",
        sa.Column(
            "conditions",
            sa.JSON(),
            nullable=True,
        ),
    )

    logic_operator_enum = sa.Enum(
        "AND",
        "OR",
        name="logic_operator_enum",
    )

    logic_operator_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "automation_rules",
        sa.Column(
            "logic_operator",
            logic_operator_enum,
            nullable=True,
            server_default="AND",
        ),
    )

    # Copy existing single-condition data into the new JSON column
    connection = op.get_bind()

    connection.execute(
        text(
            """
            UPDATE automation_rules
            SET conditions = json_build_array(
                json_build_object(
                    'field', field,
                    'operator', operator,
                    'value', value
                )
            )
            """
        )
    )

    connection.execute(
        text(
            """
            UPDATE automation_rules
            SET logic_operator = 'AND'::logic_operator_enum
            """
        )
    )

    # Make new columns required
    op.alter_column(
        "automation_rules",
        "conditions",
        nullable=False,
    )

    op.alter_column(
        "automation_rules",
        "logic_operator",
        nullable=False,
        server_default=None,
    )

    # Remove old columns
    op.drop_column("automation_rules", "field")
    op.drop_column("automation_rules", "operator")
    op.drop_column("automation_rules", "value")


def downgrade() -> None:
    op.add_column(
        "automation_rules",
        sa.Column("field", sa.String(length=100), nullable=True),
    )

    op.add_column(
        "automation_rules",
        sa.Column("operator", sa.String(length=50), nullable=True),
    )

    op.add_column(
        "automation_rules",
        sa.Column("value", sa.String(length=255), nullable=True),
    )

    op.drop_column("automation_rules", "logic_operator")
    op.drop_column("automation_rules", "conditions")

    logic_operator_enum = sa.Enum(
        "AND",
        "OR",
        name="logic_operator_enum",
    )

    logic_operator_enum.drop(op.get_bind(), checkfirst=True)