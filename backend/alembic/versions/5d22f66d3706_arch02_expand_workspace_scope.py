"""ARCH-02 EXPAND — additive workspace scope, attribution columns, archive table

Purely additive. Every column added is nullable with no server default, no
existing column is altered or dropped, and no row is written. The pre-ARCH-02
application runs unchanged against the resulting schema.

Constraint policy for this revision:
  - Foreign keys ARE created here. They cost nothing to validate against an
    all-NULL column, and creating them later means validating against a fully
    backfilled table under an ACCESS EXCLUSIVE lock. They also make MIGRATE
    unable to write a workspace_id that does not resolve.
  - NOT NULL, UNIQUE, and every composite index are deferred to CONTRACT.
    Indexes in particular: building them now would only slow the bulk UPDATEs
    in Steps 3 and 4, and they would be built against data that is about to
    change.

Revision ID: <generated>
Revises: 74a07cbe5d7e
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d22f66d3706'
down_revision: Union[str, None] = '74a07cbe5d7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Ordered by change class, matching plan §B.8. The order is cosmetic here —
# every operation in this revision is independent — but it keeps this file
# readable against the same grouping Steps 3 and 4 use, where order matters.
WORKSPACE_SCOPED_TABLES: tuple[str, ...] = (
    # Group 1 — scope addition
    "conversations",
    "notifications",
    "automation_logs",
    # Group 2 — ownership transfer
    "work_items",
    "automation_rules",
    # Group 3 — cardinality change
    "ai_settings",
    "email_settings",
    "document_settings",
)

# (table, column). Every one is nullable and ON DELETE SET NULL, permanently:
# these are attribution, not scope, and a NOT NULL column cannot be the target
# of SET NULL.
ATTRIBUTION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("work_items", "created_by_user_id"),
    ("automation_rules", "created_by_user_id"),
    ("ai_settings", "updated_by_user_id"),
    ("email_settings", "updated_by_user_id"),
    ("document_settings", "updated_by_user_id"),
)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Archive table
    # ------------------------------------------------------------------
    # Created here rather than in Step 4, the revision that writes to it.
    # Step 4 is the only destructive revision in the phase; giving it a
    # landing zone that already exists and has already been migrated
    # through once means it cannot fail partway with rows deleted and
    # nowhere to put them.
    #
    # No foreign keys, by design — see the model docstring.
    op.create_table(
        "settings_migration_archive",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("settings_kind", sa.String(length=16), nullable=False),
        sa.Column("source_row_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("source_user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("source_user_email", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("winning_row_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("migration_revision", sa.String(length=64), nullable=False),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_settings_migration_archive"),
    )
    op.create_index(
        "ix_settings_migration_archive_source_user_id",
        "settings_migration_archive",
        ["source_user_id"],
    )
    op.create_index(
        "ix_settings_migration_archive_workspace_id",
        "settings_migration_archive",
        ["workspace_id"],
    )

    # ------------------------------------------------------------------
    # 2. Scope columns
    # ------------------------------------------------------------------
    for table in WORKSPACE_SCOPED_TABLES:
        op.add_column(
            table,
            sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            constraint_name=f"fk_{table}_workspace_id_workspaces",
            source_table=table,
            referent_table="workspaces",
            local_cols=["workspace_id"],
            remote_cols=["id"],
            ondelete="CASCADE",
        )

    # ------------------------------------------------------------------
    # 3. Attribution columns
    # ------------------------------------------------------------------
    # The legacy user_id columns are NOT touched. work_items.user_id and
    # work_items.created_by_user_id coexist until CONTRACT; that overlap is
    # what makes Step 3 a copy rather than a rename, and what makes this
    # revision reversible.
    for table, column in ATTRIBUTION_COLUMNS:
        op.add_column(
            table,
            sa.Column(column, sa.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            constraint_name=f"fk_{table}_{column}_users",
            source_table=table,
            referent_table="users",
            local_cols=[column],
            remote_cols=["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for table, column in reversed(ATTRIBUTION_COLUMNS):
        op.drop_constraint(f"fk_{table}_{column}_users", table, type_="foreignkey")
        op.drop_column(table, column)

    for table in reversed(WORKSPACE_SCOPED_TABLES):
        op.drop_constraint(
            f"fk_{table}_workspace_id_workspaces", table, type_="foreignkey"
        )
        op.drop_column(table, "workspace_id")

    op.drop_index(
        "ix_settings_migration_archive_workspace_id",
        table_name="settings_migration_archive",
    )
    op.drop_index(
        "ix_settings_migration_archive_source_user_id",
        table_name="settings_migration_archive",
    )
    op.drop_table("settings_migration_archive")
