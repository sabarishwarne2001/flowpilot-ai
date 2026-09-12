"""ARCH-30 Tranche 4 Steps A1 + A3 — the workspace scheduling clock, and an honest default timezone.

Revision ID: arch30_step3_workspace_clock
Revises: arch30_step2_lapsed_tier_repair
Create Date: 2026-09-12

A1 — WORKSPACE-GOVERNED EXPORT CLOCK
====================================

`export_schedules` had one time-of-day column, `hour_utc`, and the D-5 audit
found `workspaces.timezone` had no readers anywhere in the system. This adds a
second, optional clock: a schedule may name a workspace whose timezone governs
it, and a `local_hour` interpreted in that zone.

EXPAND-shaped, in the ARCH-15 sense:

  * both columns are nullable, so every existing row is already valid;
  * a CHECK enforces both-or-neither, so a half-configured clock cannot exist
    even for the length of one bad UPDATE;
  * `hour_utc` stays NOT NULL and keeps its meaning for rows that do not opt
    in, so no deploy ordering is required between this migration and the code
    that reads the new columns.

The FK is ON DELETE SET NULL rather than CASCADE, and this is the load-bearing
choice in the whole migration. CASCADE would delete an organization's export
schedule because somebody deleted a workspace — a tenant loses a warehouse feed
as a side effect of tidying up a workspace they thought was unrelated. SET NULL
would leave `clock_workspace_id` NULL with `local_hour` still set, which the
both-or-neither CHECK forbids, so a trigger clears the pair together and the
schedule falls back to its `hour_utc`. Exports keep running, one hour is
possibly wrong until somebody re-points the clock, and nothing is destroyed.

A3 — FIRST-LOGIN TIMEZONE
=========================

`users.timezone` is NOT NULL DEFAULT 'UTC', which makes two very different
states indistinguishable: "this person never chose" and "this person chose
UTC". Capturing the browser's zone needs to overwrite the first and never the
second, so the state has to be recorded rather than guessed.

`timezone_source` is that record:

    DEFAULT   — nobody has ever set this; the value is the column default.
    DETECTED  — filled in from the browser's IANA zone on first authenticated
                load, with an audit line.
    EXPLICIT  — a human chose it at PATCH /me/profile. Never overwritten.

Backfill uses the only evidence the existing rows carry: a timezone that is not
'UTC' cannot have come from the default, so it is EXPLICIT. Rows still on 'UTC'
stay DEFAULT — which means an existing user who deliberately chose UTC will be
offered detection once. That is the correct trade against the alternative
(marking every 'UTC' row EXPLICIT and permanently stranding every user who
never chose anything on a zone that is wrong for all but one of them), and the
detection path is an explicit, audited profile update rather than a silent one.

DOWNGRADE
=========

Drops the three additions. Schedules that were on a workspace clock revert to
`hour_utc`, which is still populated — that is why the column was never
relaxed. `timezone_source` is discarded; the timezones it explains stay.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "arch30_step3_workspace_clock"
down_revision = "arch30_step2_lapsed_tier_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # A1 — export_schedules clock
    # ------------------------------------------------------------------
    op.add_column(
        "export_schedules",
        sa.Column(
            "clock_workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
            comment=(
                "Workspace whose timezone governs local_hour. NULL means the "
                "schedule runs on hour_utc, which is the pre-ARCH-30-T4 "
                "behaviour and stays the default."
            ),
        ),
    )
    op.add_column(
        "export_schedules",
        sa.Column(
            "local_hour",
            sa.SmallInteger(),
            nullable=True,
            comment=(
                "Wall-clock hour in the clock workspace's timezone. DST is "
                "resolved by app.services.analytics.clock: gaps fire when the "
                "gap closes, folds fire on the first occurrence."
            ),
        ),
    )

    op.create_foreign_key(
        "fk_export_schedules_clock_workspace",
        "export_schedules",
        "workspaces",
        ["clock_workspace_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_check_constraint(
        "clock_both_or_neither",
        "export_schedules",
        "(clock_workspace_id IS NULL) = (local_hour IS NULL)",
    )
    op.create_check_constraint(
        "local_hour_in_range",
        "export_schedules",
        "local_hour IS NULL OR (local_hour >= 0 AND local_hour <= 23)",
    )

    op.create_index(
        "ix_export_schedules_clock_workspace_id",
        "export_schedules",
        ["clock_workspace_id"],
        postgresql_where=sa.text("clock_workspace_id IS NOT NULL"),
    )

    # SET NULL on the FK would otherwise leave local_hour orphaned and trip
    # clock_both_or_neither at the worst possible moment — inside somebody
    # else's DELETE. Clearing the pair keeps the schedule alive on hour_utc.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION export_schedules_clear_orphan_clock()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.clock_workspace_id IS NULL AND NEW.local_hour IS NOT NULL
            THEN
                NEW.local_hour := NULL;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_export_schedules_clear_orphan_clock
        BEFORE UPDATE ON export_schedules
        FOR EACH ROW
        EXECUTE FUNCTION export_schedules_clear_orphan_clock();
        """
    )

    # ------------------------------------------------------------------
    # A3 — users.timezone_source
    # ------------------------------------------------------------------
    op.add_column(
        "users",
        sa.Column(
            "timezone_source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'DEFAULT'"),
            comment=(
                "How users.timezone got its value. DEFAULT means nobody has "
                "chosen; only a DEFAULT row may be overwritten by browser "
                "detection."
            ),
        ),
    )
    op.create_check_constraint(
        "timezone_source_known",
        "users",
        "timezone_source IN ('DEFAULT', 'DETECTED', 'EXPLICIT')",
    )

    # A non-UTC timezone on an existing row cannot have come from the column
    # default, so it was chosen. Rows still on 'UTC' stay DEFAULT; see the
    # module docstring for why that asymmetry is the intended one.
    op.execute(
        """
        UPDATE users
           SET timezone_source = 'EXPLICIT'
         WHERE timezone IS NOT NULL
           AND timezone <> 'UTC'
        """
    )


def downgrade() -> None:
    op.drop_constraint("timezone_source_known", "users", type_="check")
    op.drop_column("users", "timezone_source")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_export_schedules_clear_orphan_clock "
        "ON export_schedules"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS export_schedules_clear_orphan_clock()"
    )
    op.drop_index(
        "ix_export_schedules_clock_workspace_id", table_name="export_schedules"
    )
    op.drop_constraint(
        "local_hour_in_range", "export_schedules", type_="check"
    )
    op.drop_constraint(
        "clock_both_or_neither", "export_schedules", type_="check"
    )
    op.drop_constraint(
        "fk_export_schedules_clock_workspace",
        "export_schedules",
        type_="foreignkey",
    )
    op.drop_column("export_schedules", "local_hour")
    op.drop_column("export_schedules", "clock_workspace_id")