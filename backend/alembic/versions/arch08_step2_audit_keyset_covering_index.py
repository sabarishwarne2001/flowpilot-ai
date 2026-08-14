"""arch08_step2_audit_keyset_covering_index

ARCH-08 Step 2 — Keyset Pagination. Creates concurrent covering index
ix_audit_logs_organization_id_created_at_id.
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "arch08_step2_keyset_index"
down_revision: Union[str, None] = "2caf2a1cfca1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_audit_logs_organization_id_created_at_id",
            "audit_logs",
            ["organization_id", sa.text("created_at DESC"), sa.text("id DESC")],
            unique=False,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_audit_logs_organization_id_created_at_id",
            table_name="audit_logs",
            postgresql_concurrently=True,
        )