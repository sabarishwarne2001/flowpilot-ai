"""workspace ownership contract

Completes the Phase 1 Expand-Migrate-Contract cycle for workspace ownership.

Expand   (already applied, revision b13c7b21bec9 "add workspace members"):
         the workspace_members junction table.

Migrate  (this revision): backfill WorkspaceMember(OWNER) rows from the legacy
         workspaces.user_id column, idempotently.

Contract (this revision): drop the foreign key, drop the unique index, and
         relax NOT NULL on workspaces.user_id. The column is retained as an
         inert, nullable rollback artifact for one release and is never written
         by application code after this revision.

After this revision, workspace ownership is represented by exactly one thing:
a workspace_members row with role = 'OWNER' and is_active = true.

Revision ID: c4e81a9f2b73
Revises: bb57122ca97e
Create Date: 2026-08-05
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# ---------------------------------------------------------------------------
# Revision identifiers, used by Alembic.
# ---------------------------------------------------------------------------

revision = "c4e81a9f2b73"
down_revision = "bb57122ca97e"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration")


# ---------------------------------------------------------------------------
# Object names.
#
# SQLAlchemy emits a single UNIQUE INDEX (not a separate UNIQUE constraint)
# when a column declares both unique=True and index=True, which is the case for
# workspaces.user_id in models/workspace.py. The constraint name is also
# dropped defensively in case the original revision declared it differently.
# ---------------------------------------------------------------------------

FK_NAME = "workspaces_user_id_fkey"
UNIQUE_INDEX_NAME = "ix_workspaces_user_id"
UNIQUE_CONSTRAINT_NAME = "workspaces_user_id_key"


def upgrade() -> None:
    conn = op.get_bind()

    # =======================================================================
    # STEP 1 - MIGRATE
    #
    # Backfill an OWNER membership for every workspace that still carries a
    # legacy user_id and does not already have a corresponding membership row.
    #
    # Idempotent: the NOT EXISTS guard makes re-running a no-op and respects
    # the uq_user_workspace_membership unique constraint.
    #
    # The INNER JOIN against users deliberately skips workspaces whose user_id
    # references a row that no longer exists. Those are detected and reported
    # in STEP 2 rather than being silently backfilled.
    #
    # Enum literals are left uncast: PostgreSQL coerces unknown-type string
    # literals to the target column type, so this does not depend on the
    # enum type name.
    #
    # gen_random_uuid() is built in from PostgreSQL 13 onward.
    # =======================================================================

    result = conn.execute(
        sa.text(
            """
            INSERT INTO workspace_members (
                id, user_id, workspace_id, role, is_active, created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                w.user_id,
                w.id,
                'OWNER',
                true,
                now(),
                now()
            FROM workspaces AS w
            INNER JOIN users AS u ON u.id = w.user_id
            WHERE w.user_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM workspace_members AS m
                  WHERE m.user_id = w.user_id
                    AND m.workspace_id = w.id
              )
            """
        )
    )

    logger.info(
        "workspace ownership contract: backfilled %s OWNER membership row(s) "
        "from the legacy workspaces.user_id column.",
        result.rowcount,
    )

    # =======================================================================
    # STEP 2 - VERIFY
    #
    # Refuse to contract the schema if any workspace would be left without an
    # active OWNER. Once the legacy column stops being honoured it is the only
    # remaining ownership record, so proceeding would lock every member of that
    # workspace out with no self-service recovery path.
    #
    # Raising here aborts the transaction and leaves the schema untouched.
    # =======================================================================

    orphaned = conn.execute(
        sa.text(
            """
            SELECT w.id, w.workspace_name, w.user_id
            FROM workspaces AS w
            WHERE NOT EXISTS (
                SELECT 1
                FROM workspace_members AS m
                WHERE m.workspace_id = w.id
                  AND m.role = 'OWNER'
                  AND m.is_active = true
            )
            """
        )
    ).fetchall()

    if orphaned:
        details = "\n".join(
            f"    - workspace {row[0]} ({row[1]!r}), legacy user_id={row[2]}"
            for row in orphaned
        )
        raise RuntimeError(
            "Migration aborted. The following workspaces have no active OWNER "
            "membership and would be orphaned by the Contract step:\n"
            f"{details}\n\n"
            "Resolve these rows manually (assign an OWNER membership, or delete "
            "the abandoned workspace) and re-run. No schema changes were applied."
        )

    logger.info(
        "workspace ownership contract: verified every workspace has an active OWNER."
    )

    # =======================================================================
    # STEP 3 - CONTRACT
    #
    # Dropping the foreign key also removes its ON DELETE CASCADE. That cascade
    # was a data-loss hazard: deleting a user destroyed the entire workspace,
    # including the records of every other member in it. Member lifecycle is now
    # governed solely by workspace_members, whose own foreign key to users
    # retains an appropriate CASCADE.
    # =======================================================================

    op.execute(f"ALTER TABLE workspaces DROP CONSTRAINT IF EXISTS {FK_NAME}")
    op.execute(
        f"ALTER TABLE workspaces DROP CONSTRAINT IF EXISTS {UNIQUE_CONSTRAINT_NAME}"
    )
    op.execute(f"DROP INDEX IF EXISTS {UNIQUE_INDEX_NAME}")

    op.alter_column(
        "workspaces",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    logger.info(
        "workspace ownership contract: workspaces.user_id is now nullable and "
        "unconstrained. Ownership resolves exclusively via workspace_members."
    )


def downgrade() -> None:
    """
    Conditionally reversible.

    Restores the 1:1 ownership model. That model cannot represent a user who
    owns more than one workspace, so if any such user exists the downgrade
    aborts rather than destroying membership data.
    """
    conn = op.get_bind()

    # -----------------------------------------------------------------------
    # Refuse to downgrade if the multi-workspace capability has been used.
    # -----------------------------------------------------------------------

    multi_owners = conn.execute(
        sa.text(
            """
            SELECT user_id, COUNT(*) AS owned
            FROM workspace_members
            WHERE role = 'OWNER'
              AND is_active = true
            GROUP BY user_id
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()

    if multi_owners:
        details = "\n".join(
            f"    - user {row[0]} owns {row[1]} workspaces" for row in multi_owners
        )
        raise RuntimeError(
            "Downgrade aborted. The legacy schema enforces one workspace per "
            "user via a unique index, but the following users now own multiple "
            f"workspaces:\n{details}\n\n"
            "Downgrading would require destroying workspaces. Resolve manually "
            "if a rollback is genuinely intended."
        )

    # -----------------------------------------------------------------------
    # Repopulate the legacy column from the earliest active OWNER membership.
    # -----------------------------------------------------------------------

    conn.execute(
        sa.text(
            """
            UPDATE workspaces AS w
            SET user_id = sub.user_id
            FROM (
                SELECT DISTINCT ON (workspace_id) workspace_id, user_id
                FROM workspace_members
                WHERE role = 'OWNER'
                  AND is_active = true
                ORDER BY workspace_id, created_at ASC
            ) AS sub
            WHERE w.id = sub.workspace_id
              AND w.user_id IS NULL
            """
        )
    )

    still_null = conn.execute(
        sa.text("SELECT COUNT(*) FROM workspaces WHERE user_id IS NULL")
    ).scalar()

    if still_null:
        raise RuntimeError(
            f"Downgrade aborted. {still_null} workspace(s) could not be assigned "
            "a legacy owner, so NOT NULL cannot be restored."
        )

    op.alter_column(
        "workspaces",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.create_index(
        UNIQUE_INDEX_NAME,
        "workspaces",
        ["user_id"],
        unique=True,
    )

    op.create_foreign_key(
        FK_NAME,
        "workspaces",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )