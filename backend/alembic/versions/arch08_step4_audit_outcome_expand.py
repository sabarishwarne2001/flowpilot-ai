"""arch08_step4_audit_outcome_expand

ARCH-08 Step 4 — EXPAND. Adds audit_outcome enum, nullable outcome column with
server default ALLOWED, and ACCESSED verb to audit_action.
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "arch08_step4_outcome_expand"
down_revision: Union[str, None] = "arch08_step3_enum_expand"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AUDIT_OUTCOME = sa.Enum("ALLOWED", "DENIED", name="audit_outcome")


def upgrade() -> None:
    AUDIT_OUTCOME.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "audit_logs",
        sa.Column(
            "outcome",
            AUDIT_OUTCOME,
            nullable=True,
            server_default=sa.text("'ALLOWED'"),
        ),
    )

    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'ACCESSED'")


def downgrade() -> None:
    op.drop_column("audit_logs", "outcome")
    AUDIT_OUTCOME.drop(op.get_bind(), checkfirst=True)