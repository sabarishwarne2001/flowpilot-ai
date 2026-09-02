"""arch06_step4_migrate_notifications_organization_id

Revision ID: 9b2f6d8e14a7
Revises: c8e4a1f7b930
Create Date: 2026-08-15 09:00:00.000000

ARCH-06 Step 4 (MIGRATE) — backfill notifications.organization_id.

Data only. No DDL. Derives organization_id from
workspace_id -> workspaces.organization_id for every existing row, which is
every row: Step 3 made organization-level notifications representable, but
nothing writes one yet — request_email_change's notification-adjacent Step 6
sibling and any org-scoped notification path both land after this step, so
every row this migration touches today has a real workspace_id set by
create_notification (app/crud/notification.py, still unchanged as of this
step). A.1.3 recorded 15 such rows at Step 0; this migration does not assume
that count still holds and re-derives it at runtime instead.

WHY THE JOIN CANNOT ORPHAN A ROW, VERIFIED RATHER THAN ASSUMED
-------------------------------------------------------------------
notifications.workspace_id carries `ON DELETE CASCADE` to workspaces.id (see
the table's FK, unchanged since before ARCH-06). A notification whose
workspace was deleted would have been deleted with it — there is no code path
that leaves a notification pointing at a workspace that no longer exists. The
JOIN below therefore cannot fail to match any row with a non-NULL
workspace_id, and `_guard_no_orphaned_workspace_id` below checks this
directly rather than trusting the FK's presence in the schema: the FK
guarantees integrity of DATA WRITTEN THROUGH IT, not of rows that predate the
constraint or arrived through some path that bypassed the ORM (a bulk load, a
manual INSERT during an incident). ARCH-04 Step 4's identical orphan check on
workspace_invitations existed for exactly this reason, and it is cheap
insurance here for the same one.

`workspaces.organization_id` is itself NOT NULL (unchanged since ARCH-01) —
A.1.2 already confirmed 0 workspaces violate that — so a matched row can never
carry a NULL organization_id out of the JOIN. This migration's postcondition
does not re-litigate A.1.2; it asserts the thing that could actually still be
false: that this migration's own UPDATE reached every row it was supposed to.

THE ONE ROW SHAPE THIS MIGRATION CANNOT FIX, AND DELIBERATELY DOES NOT TRY TO
-------------------------------------------------------------------------------
A row with workspace_id NULL AND organization_id NULL cannot be backfilled by
this query — the JOIN predicate requires a workspace_id to match against.
Such a row does not exist in this database as of this revision (nothing
before Step 6 can produce one), so the postcondition below catching it is not
a live case, it is a tripwire: if it ever fires, the honest reading is Step 6
shipped out of order relative to this migration, or a row was written outside
the application entirely. Either way, inventing a value here would be
guessing at a tenant this migration has no way to know, which is the same
refusal `arch04_step4_migrate_invitation_backfill.py` states for its own
NULL organization_id precondition ("This migration will not invent a
tenant"). The correct fix is upstream of this file, not a default coded into
it.

Idempotent: the UPDATE's WHERE clause only touches rows where
organization_id IS NULL, so a re-run after a partial or already-complete
backfill updates zero additional rows and is a safe no-op — matching
178b3331a95c (ARCH-02 Group 1+2)'s identical `IS NULL` idempotency guard,
just on one column on one table instead of five.

VERIFIED before writing this file, not assumed:
    - configure_mappers() clean, unchanged by this step (no model edit).
    - This revision performs no DDL, so it introduces no new name to check
      for length or collision.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "9b2f6d8e14a7"
down_revision: Union[str, None] = "c8e4a1f7b930"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.arch06.step4")


BACKFILL = sa.text("""
    UPDATE notifications n
       SET organization_id = w.organization_id
      FROM workspaces w
     WHERE w.id = n.workspace_id
       AND n.organization_id IS NULL
""")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"ARCH-06 Step 4 MIGRATE aborted: {message}")


# ============================================================================
# Pre-flight guards
# ============================================================================

def _guard_no_orphaned_workspace_id(conn) -> None:
    """
    A notification whose workspace_id names a workspace that no longer
    exists would fail the backfill JOIN silently rather than loudly — the
    row would simply stay unmatched and organization_id would stay NULL,
    surfacing only as an unexplained postcondition failure with no obvious
    cause. Checked explicitly, and named, before the UPDATE runs. See the
    module docstring for why the FK's ON DELETE CASCADE should already make
    this impossible; this guard is what confirms that holds in THIS
    database rather than trusting the schema definition alone.
    """
    orphaned = conn.execute(sa.text("""
        SELECT n.id
          FROM notifications n
          LEFT JOIN workspaces w ON w.id = n.workspace_id
         WHERE n.workspace_id IS NOT NULL
           AND w.id IS NULL
         ORDER BY n.id
    """)).fetchall()
    _assert(
        not orphaned,
        f"{len(orphaned)} notification row(s) reference a workspace that no "
        f"longer exists: {', '.join(str(r.id) for r in orphaned)}. The "
        f"backfill cannot derive an organization_id for these without "
        f"guessing one; resolve the orphaned rows manually before re-running."
    )


# ============================================================================
# Migration
# ============================================================================

def upgrade() -> None:
    conn = op.get_bind()

    _guard_no_orphaned_workspace_id(conn)

    total_before = conn.execute(
        sa.text("SELECT count(*) FROM notifications")
    ).scalar_one()
    null_before = conn.execute(
        sa.text("SELECT count(*) FROM notifications WHERE organization_id IS NULL")
    ).scalar_one()

    logger.info(
        "ARCH-06 Step 4: %s notification(s) total, %s with NULL "
        "organization_id before backfill.",
        total_before, null_before,
    )

    if null_before == 0:
        logger.info(
            "ARCH-06 Step 4: nothing to backfill (0 NULL rows, or a "
            "fresh/empty database). Skipping the UPDATE; postcondition "
            "checked below regardless."
        )
    else:
        result = conn.execute(BACKFILL)
        logger.info(
            "ARCH-06 Step 4: backfilled organization_id on %s row(s).",
            result.rowcount,
        )

    # --- Per-organization summary, matching ARCH-02 Group 1+2's
    #     "ARCH-02 summary <table> <slug>=<n>" shape -----------------------
    summary = conn.execute(sa.text("""
        SELECT o.slug, count(*) AS n
          FROM notifications n
          JOIN organizations o ON o.id = n.organization_id
         GROUP BY o.slug
         ORDER BY o.slug
    """)).fetchall()
    summary_text = ", ".join(f"{r.slug}={r.n}" for r in summary) or "(empty)"
    logger.info("ARCH-06 Step 4 summary notifications  %s", summary_text)

    # --- Postcondition: A.1.2/A.1.3's concern, closed for real -------------
    null_after = conn.execute(
        sa.text("SELECT count(*) FROM notifications WHERE organization_id IS NULL")
    ).scalar_one()
    _assert(
        null_after == 0,
        f"{null_after} notification row(s) still have a NULL "
        f"organization_id after backfill. See the module docstring's "
        f"'one row shape this migration cannot fix' section — this means "
        f"a row exists with BOTH workspace_id and organization_id NULL, "
        f"which nothing before Step 6 should be able to produce."
    )

    total_after = conn.execute(
        sa.text("SELECT count(*) FROM notifications")
    ).scalar_one()
    _assert(
        total_after == total_before,
        f"row count changed during a data-only migration: {total_before} "
        f"before, {total_after} after. This migration must never insert or "
        f"delete a notification row."
    )

    logger.info(
        "ARCH-06 Step 4 complete: %s/%s notification(s) carry a "
        "non-NULL organization_id.",
        total_after - null_after, total_after,
    )


def downgrade() -> None:
    """
    Blanking, not restoring — matching 278b3331a95c's identical downgrade
    shape. workspace_id was never touched by this migration (it was already
    NOT NULL-loosened by Step 3 and this step writes nothing to it), so
    there is nothing to restore there; only organization_id, which this
    migration is the sole writer of at this point in the chain, needs
    blanking. A re-upgrade reproduces identical values, since the derivation
    is deterministic (workspace_id -> workspaces.organization_id) and
    workspace_id itself is untouched by either direction of this migration.
    """
    conn = op.get_bind()

    result = conn.execute(sa.text(
        "UPDATE notifications SET organization_id = NULL "
        "WHERE organization_id IS NOT NULL"
    ))

    logger.info(
        "ARCH-06 Step 4 downgrade: blanked organization_id on %s row(s).",
        result.rowcount,
    )
