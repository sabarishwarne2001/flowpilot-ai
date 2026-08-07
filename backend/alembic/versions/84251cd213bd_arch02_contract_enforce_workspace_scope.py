"""ARCH-02 CONTRACT — enforce workspace scope, swap uniqueness, drop legacy

Terminal revision of the ARCH-02 schema work. After this the database matches
the models written in Step 1 and `alembic revision --autogenerate` must be
empty.

Not reversible in practice. downgrade() reconstructs the dropped user_id
columns from their attribution counterparts and is tested, but it is a
best-effort reconstruction: any row whose author was deleted between Step 5
and the downgrade has a NULL created_by_user_id and cannot satisfy the
restored NOT NULL. The assertion in downgrade() names those rows rather than
failing opaquely.

Locking: every SET NOT NULL takes ACCESS EXCLUSIVE and scans the table, and
every CREATE INDEX takes SHARE. Fine at this size. On a table of consequence
the pattern is ADD CONSTRAINT ... CHECK (col IS NOT NULL) NOT VALID, then
VALIDATE CONSTRAINT, then SET NOT NULL (PG 12+), and CREATE INDEX
CONCURRENTLY outside a transaction. Both are deliberately not used here:
they would require running this revision outside Alembic's transaction, and
losing atomicity is a worse trade than a brief lock on a table of ten rows.

Revision ID: <generated>
Revises: <step 4 revision id>
"""
from typing import Sequence, Union
import logging

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '84251cd213bd'
down_revision: Union[str, None] = '63b12b33c3b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

UUID_T = sa.UUID(as_uuid=True)

SCOPED_TABLES: tuple[str, ...] = (
    "work_items",
    "automation_rules",
    "automation_logs",
    "conversations",
    "notifications",
    "ai_settings",
    "email_settings",
    "document_settings",
)

SETTINGS_TABLES: tuple[str, ...] = (
    "ai_settings",
    "email_settings",
    "document_settings",
)

# Tables losing their legacy scope column. conversations and notifications are
# absent by design: their user_id is owner and recipient respectively, not
# scope, and both survive the phase.
LEGACY_USER_ID_TABLES: tuple[tuple[str, str], ...] = (
    ("work_items", "created_by_user_id"),
    ("automation_rules", "created_by_user_id"),
    ("ai_settings", "updated_by_user_id"),
    ("email_settings", "updated_by_user_id"),
    ("document_settings", "updated_by_user_id"),
)

# (index name, table, columns). All ascending — see the Step 2 note on
# expression indexes and autogenerate.
COMPOSITE_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("ix_work_items_workspace_created", "work_items",
     ["workspace_id", "created_at"]),
    ("ix_work_items_workspace_status", "work_items",
     ["workspace_id", "status"]),
    ("ix_automation_rules_workspace_active", "automation_rules",
     ["workspace_id", "is_active"]),
    ("ix_automation_logs_workspace_created", "automation_logs",
     ["workspace_id", "created_at"]),
    ("ix_conversations_workspace_user_updated", "conversations",
     ["workspace_id", "user_id", "updated_at"]),
    ("ix_notifications_workspace_user_read_created", "notifications",
     ["workspace_id", "user_id", "is_read", "created_at"]),
)

# Superseded by a composite that leads with workspace_id. A query filtered by
# status or is_read alone is a cross-tenant query and should not exist.
SUPERSEDED_INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_work_items_status", "work_items"),
    ("ix_automation_rules_is_active", "automation_rules"),
    ("ix_notifications_is_read", "notifications"),
)


# ======================================================================
# Helpers
# ======================================================================

def _index_exists(bind, name: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = :n"
    ), {"n": name}).scalar())


def _drop_index_checked(bind, name: str, table: str) -> None:
    """
    Fails loudly on a name mismatch. DROP INDEX IF EXISTS would be tidier and
    is exactly wrong here: a silent skip leaves a stale index in the database
    that the models do not declare, which fails the empty-autogenerate exit
    criterion with no indication of why.
    """
    if not _index_exists(bind, name):
        raise RuntimeError(
            f"Expected index {name} on {table} was not found. Confirm the "
            "real name against pg_indexes and correct this revision."
        )
    op.drop_index(name, table_name=table)


# ======================================================================
# Guards
# ======================================================================

def _guard_scope_complete(bind) -> None:
    """
    Steps 3 and 4 already asserted this. Repeated because SET NOT NULL against
    a column containing NULLs fails with a message that names the constraint,
    not the rows, and a CONTRACT revision should not inherit trust from an
    earlier one.
    """
    for table in SCOPED_TABLES:
        n = bind.execute(sa.text(
            f"SELECT count(*) FROM {table} WHERE workspace_id IS NULL"
        )).scalar_one()
        if n:
            raise RuntimeError(
                f"{table}: {n} row(s) still have a NULL workspace_id. "
                "Re-run Step 3 or Step 4 before contracting."
            )


def _guard_settings_cardinality(bind) -> None:
    for table in SETTINGS_TABLES:
        dupes = bind.execute(sa.text(f"""
            SELECT workspace_id, count(*) AS n FROM {table}
            GROUP BY workspace_id HAVING count(*) > 1
        """)).fetchall()
        if dupes:
            detail = ", ".join(f"{d.workspace_id}={d.n}" for d in dupes)
            raise RuntimeError(
                f"{table}: UNIQUE(workspace_id) cannot be created — {detail}."
            )


def _guard_attribution_preserved(bind) -> None:
    """
    Last chance to compare the attribution columns against their source. After
    the drops below there is nothing left to compare against, and a mismatch
    becomes undetectable rather than merely wrong.
    """
    for table, attribution in LEGACY_USER_ID_TABLES:
        n = bind.execute(sa.text(
            f"SELECT count(*) FROM {table} "
            f"WHERE {attribution} IS DISTINCT FROM user_id"
        )).scalar_one()
        if n:
            raise RuntimeError(
                f"{table}: {n} row(s) where {attribution} does not match the "
                "legacy user_id. Dropping user_id would lose attribution."
            )


def _assert_stored_filename_survives(bind) -> None:
    """
    Plan risk R6. This unique index is the only thing preventing two uploads
    from colliding on one storage key and silently overwriting each other on
    disk. It was nearly removed during ARCH-01 stabilization and is asserted
    here so it cannot be lost to this phase either.
    """
    row = bind.execute(sa.text("""
        SELECT indexdef FROM pg_indexes
        WHERE schemaname = 'public'
          AND indexname = 'ix_work_items_stored_filename'
    """)).scalar()
    if not row or "UNIQUE" not in row.upper():
        raise RuntimeError(
            "ix_work_items_stored_filename is missing or is no longer UNIQUE."
        )


# ======================================================================
# Migration
# ======================================================================

def upgrade() -> None:
    bind = op.get_bind()

    _guard_scope_complete(bind)
    _guard_settings_cardinality(bind)
    _guard_attribution_preserved(bind)

    # --- 1. Enforce scope -------------------------------------------
    for table in SCOPED_TABLES:
        op.alter_column(table, "workspace_id",
                        existing_type=UUID_T, nullable=False)

    # --- 2. Composite indexes ---------------------------------------
    # Built now, against final data, rather than in EXPAND against columns
    # that were about to be rewritten twice.
    for name, table, cols in COMPOSITE_INDEXES:
        op.create_index(name, table, cols)

    # --- 3. Uniqueness swap -----------------------------------------
    # The new unique index is created before the old column is dropped, so at
    # no point is a settings table without a uniqueness guarantee.
    for table in SETTINGS_TABLES:
        op.create_index(f"ix_{table}_workspace_id", table,
                        ["workspace_id"], unique=True)

    # --- 4. Retire superseded indexes -------------------------------
    for name, table in SUPERSEDED_INDEXES:
        _drop_index_checked(bind, name, table)

    # --- 5. Drop the legacy scope column ----------------------------
    # PostgreSQL drops the dependent foreign key and index with the column, so
    # neither is named here. Naming them would couple this revision to
    # constraint names generated before the naming convention existed.
    for table, _ in LEGACY_USER_ID_TABLES:
        op.drop_column(table, "user_id")
        log.info("ARCH-02 dropped legacy scope column %s.user_id", table)

    _assert_stored_filename_survives(bind)

    op.create_index(
        "ix_work_items_created_by_user_id",
        "work_items",
        ["created_by_user_id"],
    )

    op.create_index(
        "ix_automation_rules_created_by_user_id",
        "automation_rules",
        ["created_by_user_id"],
    )

    log.info("ARCH-02 CONTRACT complete — schema now matches the models.")


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index(
        "ix_work_items_created_by_user_id",
        table_name="work_items",
    )

    op.drop_index(
        "ix_automation_rules_created_by_user_id",
        table_name="automation_rules",
    )

    for table, attribution in LEGACY_USER_ID_TABLES:
        op.add_column(table, sa.Column("user_id", UUID_T, nullable=True))
        op.execute(f"UPDATE {table} SET user_id = {attribution}")

        orphans = bind.execute(sa.text(
            f"SELECT count(*) FROM {table} WHERE user_id IS NULL"
        )).scalar_one()
        if orphans:
            raise RuntimeError(
                f"{table}: {orphans} row(s) have no author to restore "
                f"user_id from — {attribution} is NULL because the account "
                "was deleted after CONTRACT ran. Reconstruction is not "
                "possible; restore from the pre-Step-5 dump instead."
            )

        op.alter_column(table, "user_id",
                        existing_type=UUID_T, nullable=False)
        op.create_index(f"ix_{table}_user_id", table, ["user_id"],
                        unique=table in SETTINGS_TABLES)
        op.create_foreign_key(
            f"fk_{table}_user_id_users", table, "users",
            ["user_id"], ["id"], ondelete="CASCADE",
        )

    for name, table in SUPERSEDED_INDEXES:
        column = name.removeprefix(f"ix_{table}_")
        op.create_index(name, table, [column])

    for table in SETTINGS_TABLES:
        op.drop_index(f"ix_{table}_workspace_id", table_name=table)

    for name, table, _ in COMPOSITE_INDEXES:
        op.drop_index(name, table_name=table)

    for table in SCOPED_TABLES:
        op.alter_column(table, "workspace_id",
                        existing_type=UUID_T, nullable=True)