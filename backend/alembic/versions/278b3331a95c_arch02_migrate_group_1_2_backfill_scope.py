"""ARCH-02 MIGRATE Group 1+2 — backfill workspace scope and attribution

Assigns every row in work_items, automation_rules, conversations,
notifications, and automation_logs to a workspace, and copies user_id into
the attribution columns added by EXPAND.

Reversible. Nothing is deleted and no legacy column is touched — the source
columns (work_items.user_id, automation_rules.user_id) remain intact until
CONTRACT, so downgrade() is a blanking of the new columns and a re-run
reproduces identical assignments. Step 4 is where reversibility ends.

Allocation rule — plan §B.5 Option B, made deterministic:

    organization := the row owner's earliest ACTIVE OrganizationMember,
                    ordered by (om.created_at, o.created_at, organization_id)
    workspace    := that organization's earliest workspace,
                    ordered by (w.created_at, w.id)

Every ORDER BY carries an id as final tiebreak so that two rows created in the
same transaction — sharing a server_default now() to microsecond precision —
cannot resolve differently between a dry run and the real run.

conversations and notifications inherit from work_items where work_item_id is
present, and fall back to the rule above otherwise. A conversation scoped to a
different workspace than the document it discusses is a retrieval leak the
foreign keys cannot express, so inheritance takes precedence.

Revision ID: <generated>
Revises: <expand revision id>
"""

import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '278b3331a95c'
down_revision: Union[str, None] = '5d22f66d3706'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")


# The allocation rule as a reusable CTE prefix. Defined once so that a change
# to the rule cannot be applied to three tables and forgotten on the fourth.
USER_TARGET_WORKSPACE = """
WITH user_org AS (
    SELECT DISTINCT ON (om.user_id)
           om.user_id,
           om.organization_id
    FROM organization_members om
    JOIN organizations o ON o.id = om.organization_id
    WHERE om.status = 'ACTIVE'
    ORDER BY om.user_id, om.created_at, o.created_at, om.organization_id
),
user_target AS (
    SELECT DISTINCT ON (uo.user_id)
           uo.user_id,
           w.id AS workspace_id
    FROM user_org uo
    JOIN workspaces w ON w.organization_id = uo.organization_id
    ORDER BY uo.user_id, w.created_at, w.id
)
"""

BACKFILLED_TABLES = (
    "work_items",
    "automation_rules",
    "conversations",
    "notifications",
    "automation_logs",
)


# ======================================================================
# Pre-flight guards
# ======================================================================

def _guard_single_organization(bind) -> None:
    """
    Option B resolves through 'the organization'. A user holding two active
    organization memberships has no such thing, and the rule would silently
    route their data into whichever tenant they joined first — the one case
    where Option B is strictly worse than Option A, named in §B.5.

    Hard failure rather than a warning: a cross-tenant misassignment is
    exactly risk R2, and it is not detectable after the fact once
    work_items.user_id is dropped in CONTRACT.
    """
    rows = bind.execute(sa.text("""
        SELECT u.email, count(*) AS orgs
        FROM organization_members om
        JOIN users u ON u.id = om.user_id
        WHERE om.status = 'ACTIVE'
        GROUP BY u.email
        HAVING count(*) > 1
        ORDER BY u.email
    """)).fetchall()

    if rows:
        detail = ", ".join(f"{r.email} ({r.orgs} orgs)" for r in rows)
        raise RuntimeError(
            "ARCH-02 §B.5 Option B is ambiguous for multi-organization "
            f"users: {detail}. Resolve by choosing a home organization per "
            "user, or switch to Option C, before re-running."
        )


def _guard_data_owners_have_a_home(bind) -> None:
    """
    A user who owns rows but holds no ACTIVE organization membership cannot be
    resolved at all. Caught here so the failure names the user, rather than
    surfacing as an opaque non-zero NULL count after five UPDATEs.
    """
    rows = bind.execute(sa.text("""
        SELECT u.email
        FROM users u
        WHERE (
                EXISTS (SELECT 1 FROM work_items       t WHERE t.user_id = u.id)
             OR EXISTS (SELECT 1 FROM automation_rules t WHERE t.user_id = u.id)
             OR EXISTS (SELECT 1 FROM conversations    t WHERE t.user_id = u.id)
             OR EXISTS (SELECT 1 FROM notifications    t WHERE t.user_id = u.id)
        )
        AND NOT EXISTS (
                SELECT 1 FROM organization_members om
                WHERE om.user_id = u.id AND om.status = 'ACTIVE'
        )
        ORDER BY u.email
    """)).fetchall()

    if rows:
        raise RuntimeError(
            "Users own tenant-scoped rows but hold no ACTIVE organization "
            f"membership: {', '.join(r.email for r in rows)}. "
            "Their data has no resolvable workspace."
        )


# ======================================================================
# Post-backfill assertions
# ======================================================================

def _assert_fully_scoped(bind) -> None:
    """Plan §C Step 3: assert zero unassigned rows before commit."""
    for table in BACKFILLED_TABLES:
        unscoped = bind.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE workspace_id IS NULL")
        ).scalar_one()
        if unscoped:
            raise RuntimeError(
                f"{table}: {unscoped} row(s) left without a workspace_id."
            )


def _assert_attribution_copied(bind) -> None:
    """
    created_by_user_id must equal the legacy user_id on every row. Verified
    against the source column while it still exists — after CONTRACT there is
    nothing left to compare against.
    """
    for table in ("work_items", "automation_rules"):
        bad = bind.execute(sa.text(f"""
            SELECT count(*) FROM {table}
            WHERE created_by_user_id IS DISTINCT FROM user_id
        """)).scalar_one()
        if bad:
            raise RuntimeError(
                f"{table}: {bad} row(s) where created_by_user_id does not "
                "match the legacy user_id."
            )


def _assert_no_split_documents(bind) -> None:
    """
    A conversation, notification, or automation log must never sit in a
    different workspace from the work item it references. Foreign keys cannot
    express this — both columns are individually valid — so it is asserted.
    """
    checks = (
        ("conversations",   "work_item_id", "work_items"),
        ("notifications",   "work_item_id", "work_items"),
    )
    for table, fk, parent in checks:
        bad = bind.execute(sa.text(f"""
            SELECT count(*)
            FROM {table} c
            JOIN {parent} p ON p.id = c.{fk}
            WHERE c.workspace_id IS DISTINCT FROM p.workspace_id
        """)).scalar_one()
        if bad:
            raise RuntimeError(
                f"{table}: {bad} row(s) scoped to a different workspace than "
                f"their {parent} row."
            )

    # automation_logs derives from the rule, so this compares two independent
    # derivation paths. A mismatch means a rule fired on a document outside
    # its own workspace — pre-existing corruption that predates this phase and
    # needs a decision, not a default.
    bad = bind.execute(sa.text("""
        SELECT count(*)
        FROM automation_logs al
        JOIN work_items wi ON wi.id = al.work_item_id
        WHERE al.workspace_id IS DISTINCT FROM wi.workspace_id
    """)).scalar_one()
    if bad:
        raise RuntimeError(
            f"automation_logs: {bad} row(s) where the rule's workspace and "
            "the work item's workspace disagree. A rule fired across a "
            "tenant boundary before ARCH-02; resolve manually."
        )


def _assert_owners_can_see_their_data(bind) -> None:
    """
    The failure mode specific to Option B.

    Option B picks the organization's earliest workspace regardless of whether
    the owner ever joined it. A user who joined only the second workspace has
    their documents assigned to the first — where, unless they hold org
    OWNER/ADMIN, they cannot see them. The rows are not lost, but they are
    invisible, which is indistinguishable from lost to the person reporting it.

    Visibility means an ACTIVE workspace membership, or the derived workspace
    ADMIN that org OWNER and ADMIN receive under ARCH-01.
    """
    rows = bind.execute(sa.text("""
        WITH owned AS (
            SELECT user_id, workspace_id FROM work_items
            UNION SELECT user_id, workspace_id FROM automation_rules
            UNION SELECT user_id, workspace_id FROM conversations
            UNION SELECT user_id, workspace_id FROM notifications
        )
        SELECT u.email, w.slug AS workspace
        FROM owned x
        JOIN users u      ON u.id = x.user_id
        JOIN workspaces w ON w.id = x.workspace_id
        WHERE NOT EXISTS (
                SELECT 1 FROM workspace_members wm
                WHERE wm.user_id = x.user_id
                  AND wm.workspace_id = x.workspace_id
                  AND wm.status = 'ACTIVE'
        )
        AND NOT EXISTS (
                SELECT 1 FROM organization_members om
                WHERE om.user_id = x.user_id
                  AND om.organization_id = w.organization_id
                  AND om.status = 'ACTIVE'
                  AND om.role IN ('OWNER', 'ADMIN')
        )
        ORDER BY u.email, w.slug
    """)).fetchall()

    if rows:
        detail = "; ".join(f"{r.email} -> {r.workspace}" for r in rows)
        raise RuntimeError(
            "Option B assigned rows to workspaces their owner cannot access: "
            f"{detail}. Grant membership, or reconsider §B.5."
        )


def _report_inactive_targets(bind) -> None:
    """Advisory only — Option B is defined on creation order, not status."""
    rows = bind.execute(sa.text("""
        SELECT w.slug, w.status, count(*) AS rows_assigned
        FROM work_items wi
        JOIN workspaces w ON w.id = wi.workspace_id
        WHERE w.status <> 'ACTIVE'
        GROUP BY w.slug, w.status
    """)).fetchall()
    for r in rows:
        log.warning(
            "ARCH-02: %s row(s) assigned to non-ACTIVE workspace %s (%s)",
            r.rows_assigned, r.slug, r.status,
        )


# ======================================================================
# Assignment log
# ======================================================================

def _log_assignments(bind) -> None:
    """
    Per-row audit for the two ownership-transfer tables, and per-table
    summaries for the rest. Emitted after the UPDATEs so it reports committed
    state rather than intent — a log of what was computed is worth much less
    than a log of what landed.
    """
    rows = bind.execute(sa.text("""
        SELECT wi.id, wi.original_filename, u.email, o.slug AS org, w.slug AS ws
        FROM work_items wi
        JOIN workspaces w    ON w.id = wi.workspace_id
        JOIN organizations o ON o.id = w.organization_id
        LEFT JOIN users u    ON u.id = wi.created_by_user_id
        ORDER BY wi.created_at
    """)).fetchall()
    for r in rows:
        log.info(
            "ARCH-02 assign work_item %s (%s) owner=%s -> %s/%s",
            r.id, r.original_filename, r.email, r.org, r.ws,
        )

    rows = bind.execute(sa.text("""
        SELECT ar.id, ar.name, u.email, o.slug AS org, w.slug AS ws
        FROM automation_rules ar
        JOIN workspaces w    ON w.id = ar.workspace_id
        JOIN organizations o ON o.id = w.organization_id
        LEFT JOIN users u    ON u.id = ar.created_by_user_id
        ORDER BY ar.created_at
    """)).fetchall()
    for r in rows:
        log.info(
            "ARCH-02 assign automation_rule %s (%s) owner=%s -> %s/%s",
            r.id, r.name, r.email, r.org, r.ws,
        )

    for table in BACKFILLED_TABLES:
        rows = bind.execute(sa.text(f"""
            SELECT w.slug, count(*) AS n
            FROM {table} t JOIN workspaces w ON w.id = t.workspace_id
            GROUP BY w.slug ORDER BY w.slug
        """)).fetchall()
        summary = ", ".join(f"{r.slug}={r.n}" for r in rows) or "(empty)"
        log.info("ARCH-02 summary %-18s %s", table, summary)


# ======================================================================
# Migration
# ======================================================================

def upgrade() -> None:
    bind = op.get_bind()

    _guard_single_organization(bind)
    _guard_data_owners_have_a_home(bind)

    # --- Group 2: ownership transfer ---------------------------------
    # Scope and attribution set in one statement. Splitting them would allow a
    # state where a row is scoped but unattributed, which no assertion here
    # would distinguish from a row that legitimately has a NULL author.
    op.execute(USER_TARGET_WORKSPACE + """
        UPDATE work_items wi
        SET workspace_id       = ut.workspace_id,
            created_by_user_id = wi.user_id
        FROM user_target ut
        WHERE ut.user_id = wi.user_id
          AND wi.workspace_id IS NULL
    """)

    op.execute(USER_TARGET_WORKSPACE + """
        UPDATE automation_rules ar
        SET workspace_id       = ut.workspace_id,
            created_by_user_id = ar.user_id
        FROM user_target ut
        WHERE ut.user_id = ar.user_id
          AND ar.workspace_id IS NULL
    """)

    # --- Group 1: scope addition -------------------------------------
    # Pass 1 inherits from the parent document; pass 2 resolves the remainder
    # through the owner. Order matters: the IS NULL predicate in pass 2 is what
    # stops it overwriting an inherited assignment.
    op.execute("""
        UPDATE conversations c
        SET workspace_id = wi.workspace_id
        FROM work_items wi
        WHERE wi.id = c.work_item_id
          AND c.workspace_id IS NULL
    """)
    op.execute(USER_TARGET_WORKSPACE + """
        UPDATE conversations c
        SET workspace_id = ut.workspace_id
        FROM user_target ut
        WHERE ut.user_id = c.user_id
          AND c.workspace_id IS NULL
    """)

    op.execute("""
        UPDATE notifications n
        SET workspace_id = wi.workspace_id
        FROM work_items wi
        WHERE wi.id = n.work_item_id
          AND n.workspace_id IS NULL
    """)
    op.execute(USER_TARGET_WORKSPACE + """
        UPDATE notifications n
        SET workspace_id = ut.workspace_id
        FROM user_target ut
        WHERE ut.user_id = n.user_id
          AND n.workspace_id IS NULL
    """)

    # automation_logs has no user_id. Derived from the rule that fired, not
    # the document acted upon: a log records a rule execution, and the rule's
    # workspace is the authoritative scope.
    op.execute("""
        UPDATE automation_logs al
        SET workspace_id = ar.workspace_id
        FROM automation_rules ar
        WHERE ar.id = al.rule_id
          AND al.workspace_id IS NULL
    """)

    # --- Gates -------------------------------------------------------
    _assert_fully_scoped(bind)
    _assert_attribution_copied(bind)
    _assert_no_split_documents(bind)
    _assert_owners_can_see_their_data(bind)
    _report_inactive_targets(bind)
    _log_assignments(bind)


def downgrade() -> None:
    # Blanking, not restoring: the source columns were never modified.
    op.execute("UPDATE work_items       SET workspace_id = NULL, created_by_user_id = NULL")
    op.execute("UPDATE automation_rules SET workspace_id = NULL, created_by_user_id = NULL")
    op.execute("UPDATE conversations    SET workspace_id = NULL")
    op.execute("UPDATE notifications    SET workspace_id = NULL")
    op.execute("UPDATE automation_logs  SET workspace_id = NULL")
