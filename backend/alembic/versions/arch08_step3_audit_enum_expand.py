"""arch08_step3_audit_enum_expand

ARCH-08 Step 3 — Enum EXPAND. Adds AUDIT_LOG and API_KEY to audit_resource_type,
and EXPORTED and ROTATED to audit_action using autocommit_block().
"""

from typing import Sequence, Union
from alembic import op

revision: str = "arch08_step3_enum_expand"
down_revision: Union[str, None] = "arch08_step2_keyset_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ADDITIONS = (
    ("audit_resource_type", "AUDIT_LOG"),
    ("audit_resource_type", "API_KEY"),
    ("audit_action", "EXPORTED"),
    ("audit_action", "ROTATED"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for type_name, value in _ADDITIONS:
            op.execute(
                f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{value}'"
            )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN RAISE NOTICE "
        "'arch08_step3: enum values are not removable; leaving in place'; "
        "END $$"
    )