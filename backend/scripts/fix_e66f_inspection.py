#!/usr/bin/env python3
"""
scripts/fix_e66f_inspection.py
Replaces swallowing try/except in e66f8636c46a with explicit table inspection
so PostgreSQL transactions never enter the aborted state.

Repository: https://github.com/sabarishwarne2001/flowpilot-ai/tree/main
"""

import sys
from pathlib import Path

# Safeguard Windows stdout encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")

root_dir = Path(__file__).parent.parent.resolve()

E66F_INSPECTION_CODE = '''"""arch01_contract_legacy_workspace_columns

Revision ID: e66f8636c46a
Revises: 638190804c7d
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = 'e66f8636c46a'
down_revision: Union[str, None] = '638190804c7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    op.alter_column("workspaces", "organization_id", nullable=False)
    op.alter_column("workspaces", "slug", nullable=False)
    op.alter_column("workspaces", "status", nullable=False)
    op.alter_column("workspace_members", "status", nullable=False)
    op.alter_column("workspace_members", "role_v2", nullable=False)

    if "workspace_invitations" in tables:
        op.alter_column("workspace_invitations", "role_v2", nullable=False)

    op.create_unique_constraint(
        "uq_workspace_organization_slug",
        "workspaces",
        ["organization_id", "slug"],
    )

    op.drop_column("workspace_members", "role")

    if "workspace_invitations" in tables:
        op.drop_column("workspace_invitations", "role")

    op.alter_column("workspace_members", "role_v2", new_column_name="role")

    if "workspace_invitations" in tables:
        op.alter_column("workspace_invitations", "role_v2", new_column_name="role")

    op.execute("DROP TYPE workspace_role")
    op.execute("ALTER TYPE workspace_role_v2 RENAME TO workspace_role")

    op.drop_column("workspaces", "company_name")
    op.drop_column("workspaces", "is_active")
    op.drop_column("workspace_members", "is_active")

    if _column_exists(bind, "workspaces", "user_id"):
        op.drop_column("workspaces", "user_id")


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    result = bind.execute(
        sa.text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = :table_name AND column_name = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    ).fetchone()
    return result is not None


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    op.add_column(
        "workspaces",
        sa.Column("company_name", sa.String(length=150), nullable=True),
    )
    op.add_column(
        "workspaces",
        sa.Column("is_active", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "workspace_members",
        sa.Column("is_active", sa.Boolean(), nullable=True),
    )

    if not _column_exists(bind, "workspaces", "user_id"):
        op.add_column(
            "workspaces",
            sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        )

    op.execute(
        """
        UPDATE workspaces w
        SET company_name = o.name,
            is_active    = (w.status = 'ACTIVE')
        FROM organizations o
        WHERE o.id = w.organization_id
        """
    )
    op.execute("UPDATE workspace_members SET is_active = (status = 'ACTIVE')")

    op.alter_column("workspaces", "company_name", nullable=False)
    op.alter_column(
        "workspaces", "is_active", nullable=False, server_default=sa.true()
    )
    op.alter_column(
        "workspace_members",
        "is_active",
        nullable=False,
        server_default=sa.true(),
    )

    op.execute("ALTER TYPE workspace_role RENAME TO workspace_role_v2")

    op.alter_column("workspace_members", "role", new_column_name="role_v2")
    if "workspace_invitations" in tables:
        op.alter_column("workspace_invitations", "role", new_column_name="role_v2")

    op.add_column(
        "workspace_members",
        sa.Column("role", sa.dialects.postgresql.ENUM("OWNER", "MANAGER", "CONTRIBUTOR", "VIEWER", name="workspace_role_legacy", create_type=False), nullable=True),
    )
    if "workspace_invitations" in tables:
        op.add_column(
            "workspace_invitations",
            sa.Column("role", sa.dialects.postgresql.ENUM("OWNER", "MANAGER", "CONTRIBUTOR", "VIEWER", name="workspace_role_legacy", create_type=False), nullable=True),
        )

    op.execute(
        """
        UPDATE workspace_members wm
        SET role = CASE
            WHEN wm.role_v2 = 'ADMIN' AND EXISTS (
                SELECT 1
                FROM organization_members om
                JOIN workspaces w ON w.organization_id = om.organization_id
                WHERE w.id = wm.workspace_id
                  AND om.user_id = wm.user_id
                  AND om.role = 'OWNER'
            ) THEN 'OWNER'::workspace_role_legacy
            WHEN wm.role_v2 = 'ADMIN'       THEN 'MANAGER'::workspace_role_legacy
            WHEN wm.role_v2 = 'CONTRIBUTOR' THEN 'CONTRIBUTOR'::workspace_role_legacy
            ELSE 'VIEWER'::workspace_role_legacy
        END
        """
    )
    if "workspace_invitations" in tables:
        op.execute(
            """
            UPDATE workspace_invitations
            SET role = CASE
                WHEN role_v2 = 'ADMIN'       THEN 'MANAGER'::workspace_role_legacy
                WHEN role_v2 = 'CONTRIBUTOR' THEN 'CONTRIBUTOR'::workspace_role_legacy
                ELSE 'VIEWER'::workspace_role_legacy
            END
            """
        )

    op.alter_column("workspace_members", "role", nullable=False)
    if "workspace_invitations" in tables:
        op.alter_column("workspace_invitations", "role", nullable=False)

    op.drop_column("workspace_members", "role_v2")
    if "workspace_invitations" in tables:
        op.drop_column("workspace_invitations", "role_v2")

    op.execute("ALTER TYPE workspace_role_legacy RENAME TO workspace_role")

    op.add_column(
        "workspace_members",
        sa.Column(
            "role_v2",
            sa.dialects.postgresql.ENUM(name="workspace_role_v2", create_type=False),
            nullable=True,
        ),
    )
    if "workspace_invitations" in tables:
        op.add_column(
            "workspace_invitations",
            sa.Column(
                "role_v2",
                sa.dialects.postgresql.ENUM(name="workspace_role_v2", create_type=False),
                nullable=True,
            ),
        )

    op.drop_constraint(
        "uq_workspace_organization_slug", "workspaces", type_="unique"
    )
    if "workspace_invitations" in tables:
        op.alter_column("workspace_invitations", "role_v2", nullable=True)
    op.alter_column("workspace_members", "status", nullable=True)
    op.alter_column("workspaces", "status", nullable=True)
    op.alter_column("workspaces", "slug", nullable=True)
    op.alter_column("workspaces", "organization_id", nullable=True)
'''

def main() -> None:
    print("=== OVERWRITING e66f8636c46a WITH INSPECTION GUARD ===")
    files = list(root_dir.rglob("*e66f8636c46a*.py"))
    for f in files:
        f.write_text(E66F_INSPECTION_CODE, encoding="utf-8")
        print(f"[SUCCESSFULLY OVERWRITTEN] {f}")

if __name__ == "__main__":
    main()
