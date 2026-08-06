"""arch01_contract_legacy_workspace_columns

ARCH-01 Step 5 of 10 — CONTRACT leg of Expand -> Migrate -> Contract.

Removes the legacy flat-workspace schema now that MIGRATE has populated and
proven the organization model. This revision is destructive: legacy
application code cannot run against the resulting schema, which is the
intended terminal state of the transformation.

Operation order is load-bearing. Every NOT NULL constraint is applied BEFORE
any column is dropped, so that a missed row aborts the migration while the
source data is still present and diagnosable. Enforce, then destroy.

The workspace_role enum is retired by swap rather than mutation. PostgreSQL
cannot remove a value from an enum type, so OWNER could never be eliminated in
place. Both workspace_members and workspace_invitations depend on the legacy
type, so both legacy columns are dropped in the same operation before the type
itself can be removed.

downgrade() restores the structure faithfully and reconstructs what it can:
company_name from organizations.name, is_active from the status enums, and
OWNER from organization_members. It cannot recover a company_name that was
edited at organization level after this revision ran. Structure returns; some
history does not.

Revision ID: e66f8636c46a
Revises: 638190804c7d
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e66f8636c46a'
down_revision: Union[str, None] = '638190804c7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Legacy enum, recreated on downgrade only.
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

    # =======================================================================
    # 1. NOT NULL constraints
    #
    # Applied first, deliberately. Each is a database-level assertion that
    # MIGRATE completed. A missed row fails here, while company_name and the
    # is_active flags are still present to diagnose it.
    # =======================================================================
    op.alter_column("workspaces", "organization_id", nullable=False)
    op.alter_column("workspaces", "slug", nullable=False)
    op.alter_column("workspaces", "status", nullable=False)
    op.alter_column("workspace_members", "status", nullable=False)
    op.alter_column("workspace_members", "role_v2", nullable=False)
    op.alter_column("workspace_invitations", "role_v2", nullable=False)

    # workspace_invitations.organization_id intentionally remains nullable.
    # ARCH-04 makes it non-nullable when invitations become organization-scoped
    # with workspace grants attached.

    # =======================================================================
    # 2. Unique constraint on the public address
    #
    # (organization_id, slug) is what makes /{organization}/{workspace}/...
    # unambiguous. Organization slugs are already globally unique via the
    # index created in EXPAND.
    # =======================================================================
    op.create_unique_constraint(
        "uq_workspace_organization_slug",
        "workspaces",
        ["organization_id", "slug"],
    )

    # =======================================================================
    # 3. Enum swap
    #
    # BEFORE                              AFTER
    #   role     : workspace_role           (dropped)
    #   role_v2  : workspace_role_v2   ->   role : workspace_role
    #   TYPE workspace_role                 (dropped)
    #   TYPE workspace_role_v2         ->   renamed to workspace_role
    #
    # Both dependent tables must be cleared before the legacy type can be
    # dropped. PostgreSQL refuses while any column still references it.
    # =======================================================================
    op.drop_column("workspace_members", "role")
    op.drop_column("workspace_invitations", "role")

    op.alter_column("workspace_members", "role_v2", new_column_name="role")
    op.alter_column("workspace_invitations", "role_v2", new_column_name="role")

    op.execute("DROP TYPE workspace_role")
    op.execute("ALTER TYPE workspace_role_v2 RENAME TO workspace_role")

    # =======================================================================
    # 4. Drop legacy columns
    #
    # Nothing reads these. company_name now lives on organizations.name; both
    # is_active booleans are superseded by their status enums.
    # =======================================================================
    op.drop_column("workspaces", "company_name")
    op.drop_column("workspaces", "is_active")
    op.drop_column("workspace_members", "is_active")

    # --- BEGIN conditional block: deprecated Sprint-1 fallback -------------
    # Delete this block entirely if pre-flight (c) showed no workspaces.user_id
    # column. Guarded with an existence check so it is safe either way.
    if _column_exists(bind, "workspaces", "user_id"):
        op.drop_column("workspaces", "user_id")
    # --- END conditional block ---------------------------------------------


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    """Returns True if the given column is present on the given table."""
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
    """
    Restores the legacy flat-workspace schema.

    Structure is restored exactly. Data is reconstructed by inverting the
    MIGRATE mapping:

      company_name             <- organizations.name
      workspaces.is_active     <- status = 'ACTIVE'
      members.is_active        <- status = 'ACTIVE'
      legacy role              <- ADMIN becomes OWNER where the member holds
                                  OrganizationRole.OWNER, otherwise MANAGER

    A company_name edited at organization level after CONTRACT ran is not
    recoverable, and the ADMIN -> OWNER/MANAGER split relies on
    organization_members remaining intact.
    """
    bind = op.get_bind()

    # =======================================================================
    # 1. Restore legacy columns (nullable first, then backfill)
    # =======================================================================
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

    # --- BEGIN conditional block: mirrors the upgrade() block --------------
    # Delete this block if you deleted the corresponding one in upgrade().
    if not _column_exists(bind, "workspaces", "user_id"):
        op.add_column(
            "workspaces",
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
    # --- END conditional block ---------------------------------------------

    op.execute(
        """
        UPDATE workspaces w
        SET company_name = o.name,
            is_active    = (w.status = 'ACTIVE')
        FROM organizations o
        WHERE o.id = w.organization_id
        """
    )
    op.execute(
        "UPDATE workspace_members SET is_active = (status = 'ACTIVE')"
    )

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

    # =======================================================================
    # 2. Reverse the enum swap
    #
    # The current type is named workspace_role but holds the new value set.
    # Rename it back to workspace_role_v2, recreate the legacy type under a
    # temporary name, migrate the columns across, then rename into place.
    # =======================================================================
    op.execute("ALTER TYPE workspace_role RENAME TO workspace_role_v2")
    LEGACY_WORKSPACE_ROLE.create(bind, checkfirst=True)

    op.alter_column("workspace_members", "role", new_column_name="role_v2")
    op.alter_column("workspace_invitations", "role", new_column_name="role_v2")

    op.add_column(
        "workspace_members",
        sa.Column("role", LEGACY_WORKSPACE_ROLE, nullable=True),
    )
    op.add_column(
        "workspace_invitations",
        sa.Column("role", LEGACY_WORKSPACE_ROLE, nullable=True),
    )

    # ADMIN splits back into OWNER or MANAGER. organization_members is the
    # only surviving record of which members were owners.
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
    op.alter_column("workspace_invitations", "role", nullable=False)

    op.drop_column("workspace_members", "role_v2")
    op.drop_column("workspace_invitations", "role_v2")

    # Restore the canonical type name for the legacy value set. The EXPAND
    # revision's downgrade drops workspace_role_v2 afterwards.
    op.execute("ALTER TYPE workspace_role_legacy RENAME TO workspace_role")

    # Re-add role_v2 columns so the EXPAND downgrade finds what it expects.
    op.add_column(
        "workspace_members",
        sa.Column(
            "role_v2",
            postgresql.ENUM(name="workspace_role_v2", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "workspace_invitations",
        sa.Column(
            "role_v2",
            postgresql.ENUM(name="workspace_role_v2", create_type=False),
            nullable=True,
        ),
    )

    # =======================================================================
    # 3. Reverse constraints
    # =======================================================================
    op.drop_constraint(
        "uq_workspace_organization_slug", "workspaces", type_="unique"
    )
    op.alter_column("workspace_invitations", "role_v2", nullable=True)
    op.alter_column("workspace_members", "status", nullable=True)
    op.alter_column("workspaces", "status", nullable=True)
    op.alter_column("workspaces", "slug", nullable=True)
    op.alter_column("workspaces", "organization_id", nullable=True)