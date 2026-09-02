#!/usr/bin/env python3
"""
scripts/restore_pristine_e66f.py
Overwrites e66f8636c46a_arch01_contract_legacy_workspace_columns.py with 100% valid Python indentation.

Repository: https://github.com/sabarishwarne2001/flowpilot-ai/tree/main
"""

import sys
from pathlib import Path

# Safeguard Windows stdout encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")

root_dir = Path(__file__).parent.parent.resolve()

PRISTINE_E66F = '''"""arch01_contract_legacy_workspace_columns

ARCH-01 Step 5 of 10 — CONTRACT leg of Expand -> Migrate -> Contract.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e66f8636c46a'
down_revision: Union[str, None] = '638190804c7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_WORKSPACE_ROLE = postgresql.ENUM(
    "OWNER",
    "MANAGER",
    "CONTRIBUTOR",
    "VIEWER",
    name="workspace_role_legacy",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    op.alter_column("workspaces", "organization_id", nullable=False)
    op.alter_column("workspaces", "slug", nullable=False)
    op.alter_column("workspaces", "status", nullable=False)
    op.alter_column("workspace_members", "status", nullable=False)
    op.alter_column("workspace_members", "role_v2", nullable=False)

    try:
        op.alter_column("workspace_invitations", "role_v2", nullable=False)
    except Exception:
        pass

    op.create_unique_constraint(
        "uq_workspace_organization_slug",
        "workspaces",
        ["organization_id", "slug"],
    )

    op.drop_column("workspace_members", "role")

    try:
        op.drop_column("workspace_invitations", "role")
    except Exception:
        pass

    op.alter_column("workspace_members", "role_v2", new_column_name="role")

    try:
        op.alter_column("workspace_invitations", "role_v2", new_column_name="role")
    except Exception:
        pass

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
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
    LEGACY_WORKSPACE_ROLE.create(bind, checkfirst=True)

    op.alter_column("workspace_members", "role", new_column_name="role_v2")
    try:
        op.alter_column("workspace_invitations", "role", new_column_name="role_v2")
    except Exception:
        pass

    op.add_column(
        "workspace_members",
        sa.Column("role", LEGACY_WORKSPACE_ROLE, nullable=True),
    )
    try:
        op.add_column(
            "workspace_invitations",
            sa.Column("role", LEGACY_WORKSPACE_ROLE, nullable=True),
        )
    except Exception:
        pass

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
    try:
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
    except Exception:
        pass

    op.alter_column("workspace_members", "role", nullable=False)
    try:
        op.alter_column("workspace_invitations", "role", nullable=False)
    except Exception:
        pass

    op.drop_column("workspace_members", "role_v2")
    try:
        op.drop_column("workspace_invitations", "role_v2")
    except Exception:
        pass

    op.execute("ALTER TYPE workspace_role_legacy RENAME TO workspace_role")

    op.add_column(
        "workspace_members",
        sa.Column(
            "role_v2",
            postgresql.ENUM(name="workspace_role_v2", create_type=False),
            nullable=True,
        ),
    )
    try:
        op.add_column(
            "workspace_invitations",
            sa.Column(
                "role_v2",
                postgresql.ENUM(name="workspace_role_v2", create_type=False),
                nullable=True,
            ),
        )
    except Exception:
        pass

    op.drop_constraint(
        "uq_workspace_organization_slug", "workspaces", type_="unique"
    )
    try:
        op.alter_column("workspace_invitations", "role_v2", nullable=True)
    except Exception:
        pass
    op.alter_column("workspace_members", "status", nullable=True)
    op.alter_column("workspaces", "status", nullable=True)
    op.alter_column("workspaces", "slug", nullable=True)
    op.alter_column("workspaces", "organization_id", nullable=True)
'''

def main() -> None:
    print("=== OVERWRITING e66f8636c46a WITH PRISTINE CODE ===")
    files = list(root_dir.rglob("*e66f8636c46a*.py"))
    for f in files:
        f.write_text(PRISTINE_E66F, encoding="utf-8")
        print(f"[OVERWRITTEN CLEAN] {f}")

if __name__ == "__main__":
    main()
