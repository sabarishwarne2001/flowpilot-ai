"""ARCH-10 Step 2 — usage_events (EXPAND)

Revision ID: arch10_step2_usage_expand
Revises: arch09_scope_ck_repair
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch10_step2_usage_expand"
down_revision = "arch09_scope_ck_repair"
branch_labels = None
depends_on = None

IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION usage_events_immutable()
RETURNS TRIGGER AS $$
BEGIN
    -- Allow deletion only when the parent organization is being cascade-deleted
    IF (TG_OP = 'DELETE') THEN
        IF EXISTS (SELECT 1 FROM organizations WHERE id = OLD.organization_id) THEN
            RAISE EXCEPTION
                'usage_events is append-only; DELETE is not permitted (row %)',
                OLD.id
                USING ERRCODE = '42501';
        END IF;
        RETURN OLD;
    END IF;

    -- ARCH-14 folds rows into rollups by stamping aggregated_at.
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
    op.create_table(
        "usage_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "seq", sa.BigInteger(), sa.Identity(always=False, start=1), nullable=False
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("api_key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("aggregated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_usage_events_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_usage_events_workspace_id_workspaces",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_usage_events_job_id_jobs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_usage_events_actor_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["api_key_id"],
            ["api_keys.id"],
            name="fk_usage_events_api_key_id_api_keys",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_usage_events_quantity_positive"),
        sa.CheckConstraint(
            "cost_micros IS NULL OR cost_micros >= 0",
            name="ck_usage_events_cost_non_negative",
        ),
        sa.CheckConstraint(
            "num_nonnulls(actor_id, api_key_id) <= 1",
            name="ck_usage_events_single_principal",
        ),
        sa.CheckConstraint(
            "details IS NULL OR jsonb_typeof(details) = 'object'",
            name="ck_usage_events_details_is_object",
        ),
        sa.CheckConstraint(
            "length(event_type) > 0", name="ck_usage_events_event_type_not_blank"
        ),
        sa.UniqueConstraint("seq", name="uq_usage_events_seq"),
    )

    op.create_index(
        "ix_usage_events_org_type_occurred_at",
        "usage_events",
        ["organization_id", "event_type", "occurred_at"],
    )
    op.execute(
        "CREATE INDEX ix_usage_events_org_occurred_at "
        "ON usage_events (organization_id, occurred_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_usage_events_workspace_occurred_at "
        "ON usage_events (workspace_id, occurred_at DESC) "
        "WHERE workspace_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_usage_events_unaggregated "
        "ON usage_events (occurred_at, seq) "
        "WHERE aggregated_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_usage_events_job_id "
        "ON usage_events (job_id) WHERE job_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_usage_events_org_idempotency_key "
        "ON usage_events (organization_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )

    op.execute(IMMUTABILITY_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_usage_events_immutable
        BEFORE UPDATE OR DELETE ON usage_events
        FOR EACH ROW EXECUTE FUNCTION usage_events_immutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_usage_events_immutable ON usage_events")
    op.execute("DROP FUNCTION IF EXISTS usage_events_immutable()")
    op.drop_table("usage_events")