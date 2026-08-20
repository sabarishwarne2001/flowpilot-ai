"""ARCH-14 Step 1b — usage_events.price_book_id, unit_price_micros (EXPAND)

Revision ID: arch14_step1b_usage_price_columns
Revises: arch14_step1_price_books
Create Date: 2026-08-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch14_step1b_usage_price_columns"
down_revision = "arch14_step1_price_books"
branch_labels = None
depends_on = None


IMMUTABILITY_FUNCTION_V2 = """
CREATE OR REPLACE FUNCTION usage_events_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        IF EXISTS (SELECT 1 FROM organizations WHERE id = OLD.organization_id) THEN
            RAISE EXCEPTION
                'usage_events is append-only; DELETE is not permitted (row %)',
                OLD.id
                USING ERRCODE = '42501';
        END IF;
        RETURN OLD;
    END IF;

    IF (
        NEW.id                   IS DISTINCT FROM OLD.id
        OR NEW.seq               IS DISTINCT FROM OLD.seq
        OR NEW.organization_id   IS DISTINCT FROM OLD.organization_id
        OR NEW.workspace_id      IS DISTINCT FROM OLD.workspace_id
        OR NEW.event_type        IS DISTINCT FROM OLD.event_type
        OR NEW.unit              IS DISTINCT FROM OLD.unit
        OR NEW.quantity          IS DISTINCT FROM OLD.quantity
        OR NEW.cost_micros       IS DISTINCT FROM OLD.cost_micros
        OR NEW.provider          IS DISTINCT FROM OLD.provider
        OR NEW.resource_type     IS DISTINCT FROM OLD.resource_type
        OR NEW.resource_id       IS DISTINCT FROM OLD.resource_id
        OR NEW.job_id            IS DISTINCT FROM OLD.job_id
        OR NEW.actor_id          IS DISTINCT FROM OLD.actor_id
        OR NEW.api_key_id        IS DISTINCT FROM OLD.api_key_id
        OR NEW.details           IS DISTINCT FROM OLD.details
        OR NEW.idempotency_key   IS DISTINCT FROM OLD.idempotency_key
        OR NEW.occurred_at       IS DISTINCT FROM OLD.occurred_at
        OR NEW.created_at        IS DISTINCT FROM OLD.created_at
        OR NEW.price_book_id     IS DISTINCT FROM OLD.price_book_id
        OR NEW.unit_price_micros IS DISTINCT FROM OLD.unit_price_micros
    ) THEN
        RAISE EXCEPTION
            'usage_events row % is immutable; only aggregated_at may be updated',
            OLD.id
            USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

IMMUTABILITY_FUNCTION_V1 = """
CREATE OR REPLACE FUNCTION usage_events_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        IF EXISTS (SELECT 1 FROM organizations WHERE id = OLD.organization_id) THEN
            RAISE EXCEPTION
                'usage_events is append-only; DELETE is not permitted (row %)',
                OLD.id
                USING ERRCODE = '42501';
        END IF;
        RETURN OLD;
    END IF;

    IF (
        NEW.id                 IS DISTINCT FROM OLD.id
        OR NEW.seq             IS DISTINCT FROM OLD.seq
        OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
        OR NEW.workspace_id    IS DISTINCT FROM OLD.workspace_id
        OR NEW.event_type      IS DISTINCT FROM OLD.event_type
        OR NEW.unit            IS DISTINCT FROM OLD.unit
        OR NEW.quantity        IS DISTINCT FROM OLD.quantity
        OR NEW.cost_micros     IS DISTINCT FROM OLD.cost_micros
        OR NEW.provider        IS DISTINCT FROM OLD.provider
        OR NEW.resource_type   IS DISTINCT FROM OLD.resource_type
        OR NEW.resource_id     IS DISTINCT FROM OLD.resource_id
        OR NEW.job_id          IS DISTINCT FROM OLD.job_id
        OR NEW.actor_id        IS DISTINCT FROM OLD.actor_id
        OR NEW.api_key_id      IS DISTINCT FROM OLD.api_key_id
        OR NEW.details         IS DISTINCT FROM OLD.details
        OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
        OR NEW.occurred_at     IS DISTINCT FROM OLD.occurred_at
        OR NEW.created_at      IS DISTINCT FROM OLD.created_at
    ) THEN
        RAISE EXCEPTION
            'usage_events row % is immutable; only aggregated_at may be updated',
            OLD.id
            USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.add_column(
        "usage_events",
        sa.Column("price_book_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "usage_events",
        sa.Column(
            "unit_price_micros", sa.Numeric(precision=20, scale=9), nullable=True
        ),
    )

    op.create_foreign_key(
        "fk_usage_events_price_book_id_price_books",
        "usage_events",
        "price_books",
        ["price_book_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.execute(
        "ALTER TABLE usage_events ADD CONSTRAINT "
        "ck_usage_events_price_pair_complete "
        "CHECK ((price_book_id IS NULL) = (unit_price_micros IS NULL)) NOT VALID"
    )
    op.execute(
        "ALTER TABLE usage_events VALIDATE CONSTRAINT "
        "ck_usage_events_price_pair_complete"
    )

    op.execute(
        "ALTER TABLE usage_events ADD CONSTRAINT "
        "ck_usage_events_cost_matches_unit_price "
        "CHECK ("
        "  unit_price_micros IS NULL "
        "  OR cost_micros IS NULL "
        "  OR cost_micros = round(quantity * unit_price_micros)"
        ") NOT VALID"
    )
    op.execute(
        "ALTER TABLE usage_events VALIDATE CONSTRAINT "
        "ck_usage_events_cost_matches_unit_price"
    )

    op.execute(
        "CREATE INDEX ix_usage_events_unpriced "
        "ON usage_events (occurred_at) "
        "WHERE price_book_id IS NULL"
    )

    op.execute(
        "ALTER TABLE usage_events DISABLE TRIGGER trg_usage_events_immutable"
    )
    op.execute(
        """
        UPDATE usage_events
           SET details = COALESCE(details, '{}'::jsonb)
                         || '{"price_source": "legacy_ai_settings"}'::jsonb
         WHERE details IS NULL OR details->>'price_source' IS NULL
        """
    )
    op.execute(
        "ALTER TABLE usage_events ENABLE TRIGGER trg_usage_events_immutable"
    )

    op.execute(IMMUTABILITY_FUNCTION_V2)


def downgrade() -> None:
    op.execute(IMMUTABILITY_FUNCTION_V1)
    op.execute("DROP INDEX IF EXISTS ix_usage_events_unpriced")
    op.execute(
        "ALTER TABLE usage_events DROP CONSTRAINT IF EXISTS "
        "ck_usage_events_cost_matches_unit_price"
    )
    op.execute(
        "ALTER TABLE usage_events DROP CONSTRAINT IF EXISTS "
        "ck_usage_events_price_pair_complete"
    )
    op.drop_constraint(
        "fk_usage_events_price_book_id_price_books",
        "usage_events",
        type_="foreignkey",
    )
    op.drop_column("usage_events", "unit_price_micros")
    op.drop_column("usage_events", "price_book_id")