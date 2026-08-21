"""ARCH-13 Step 13.1 — outbox_events.visibility (EXPAND)

One table, two audiences. `PUBLIC` is eligible for webhook delivery; `INTERNAL`
is eligible for automation only. Existing rows default to PUBLIC, which is what
they are.

Revision ID: arch13_step1_outbox_internal_events
Revises: arch14_step8_contract_ai_settings_costs
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "arch13_step1_outbox_internal_events"
down_revision = "arch14_step8_contract_ai_settings_costs"
branch_labels = None
depends_on = None


INTERNAL_EVENT_TYPES: tuple[str, ...] = (
    "work_item.enriched",
    "work_item.field_changed",
    "work_item.verification_completed",
    "work_item.verification_disagreed",
    "automation.execution_completed",
    "automation.budget_exhausted",
)


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # Dynamically drop any legacy event_type_vocabulary constraint on outbox_events
    op.execute(
        """
        DO $$
        DECLARE
            r RECORD;
        BEGIN
            FOR r IN (
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'outbox_events'::regclass
                  AND conname LIKE '%event_type_vocabulary%'
            ) LOOP
                EXECUTE 'ALTER TABLE outbox_events DROP CONSTRAINT IF EXISTS ' || quote_ident(r.conname);
            END LOOP;
        END $$;
        """
    )

    # ---- the discriminator ------------------------------------------------
    op.add_column(
        "outbox_events",
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'PUBLIC'"),
        ),
    )

    op.create_check_constraint(
        "visibility_known",
        "outbox_events",
        "visibility IN ('PUBLIC', 'INTERNAL')",
    )

    # ---- the vocabularies must not converge -------------------------------
    op.create_check_constraint(
        "visibility_vocabulary",
        "outbox_events",
        f"""
        (visibility = 'INTERNAL' AND event_type IN ({_sql_list(INTERNAL_EVENT_TYPES)}))
        OR
        (visibility = 'PUBLIC' AND event_type NOT IN ({_sql_list(INTERNAL_EVENT_TYPES)}))
        """,
    )

    # ---- claim indexes, one per audience ----------------------------------
    op.create_index(
        "ix_outbox_events_internal_claimable",
        "outbox_events",
        ["available_at", "seq"],
        postgresql_where=sa.text(
            "visibility = 'INTERNAL' AND status IN "
            "('PENDING'::outbox_event_status, 'FAILED'::outbox_event_status)"
        ),
    )
    op.create_index(
        "ix_outbox_events_public_claimable",
        "outbox_events",
        ["available_at", "seq"],
        postgresql_where=sa.text(
            "visibility = 'PUBLIC' AND status IN "
            "('PENDING'::outbox_event_status, 'FAILED'::outbox_event_status)"
        ),
    )

    op.alter_column("outbox_events", "visibility", server_default=None)


def downgrade() -> None:
    op.alter_column(
        "outbox_events", "visibility", server_default=sa.text("'PUBLIC'")
    )
    op.drop_index("ix_outbox_events_public_claimable", table_name="outbox_events")
    op.drop_index("ix_outbox_events_internal_claimable", table_name="outbox_events")
    op.drop_constraint(
        "ck_outbox_events_visibility_vocabulary", "outbox_events", type_="check"
    )
    op.drop_constraint(
        "ck_outbox_events_visibility_known", "outbox_events", type_="check"
    )

    op.execute("DELETE FROM outbox_events WHERE visibility = 'INTERNAL'")
    op.drop_column("outbox_events", "visibility")