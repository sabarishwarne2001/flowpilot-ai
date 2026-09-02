"""ARCH-14 Step 2 — usage_rollups, rollup_windows (EXPAND)

Revision ID: arch14_step2_rollups
Revises: arch14_step1b_usage_price_columns
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch14_step2_rollups"
down_revision = "arch14_step1b_usage_price_columns"
branch_labels = None
depends_on = None

NIL_UUID = "00000000-0000-0000-0000-000000000000"


SEAL_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION usage_rollups_seal_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        IF OLD.sealed_at IS NOT NULL
           AND EXISTS (SELECT 1 FROM organizations WHERE id = OLD.organization_id)
        THEN
            RAISE EXCEPTION
                'usage_rollups bucket % (% %) is sealed and cannot be deleted',
                OLD.id, OLD.granularity, OLD.bucket_start
                USING ERRCODE = '42501';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.sealed_at IS NULL THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'usage_rollups bucket % (% % org %) is sealed at %; a late event '
        'belongs in the open bucket, not in an invoiced one',
        OLD.id, OLD.granularity, OLD.bucket_start, OLD.organization_id,
        OLD.sealed_at
        USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;
"""


WINDOW_SEAL_FUNCTION = """
CREATE OR REPLACE FUNCTION rollup_windows_seal_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        IF OLD.status = 'SEALED' THEN
            RAISE EXCEPTION
                'rollup_windows % % is sealed and cannot be deleted',
                OLD.granularity, OLD.bucket_start
                USING ERRCODE = '42501';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.status <> 'SEALED' THEN
        RETURN NEW;
    END IF;

    IF (
        NEW.granularity     IS DISTINCT FROM OLD.granularity
        OR NEW.bucket_start IS DISTINCT FROM OLD.bucket_start
        OR NEW.bucket_end   IS DISTINCT FROM OLD.bucket_end
        OR NEW.status       IS DISTINCT FROM OLD.status
        OR NEW.sealed_at    IS DISTINCT FROM OLD.sealed_at
        OR NEW.event_count  IS DISTINCT FROM OLD.event_count
    ) THEN
        RAISE EXCEPTION
            'rollup_windows % % is sealed; only late_event_count and details '
            'may still be recorded',
            OLD.granularity, OLD.bucket_start
            USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "usage_rollups",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "grain",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'DETAIL'"),
        ),
        sa.Column("granularity", sa.String(length=8), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("price_book_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "unit_price_micros", sa.Numeric(precision=20, scale=9), nullable=True
        ),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "quantity",
            sa.Numeric(precision=30, scale=6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cost_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "event_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "estimated_quantity",
            sa.Numeric(precision=30, scale=6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "estimated_cost_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "estimated_event_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "late_event_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "late_quantity",
            sa.Numeric(precision=30, scale=6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "late_cost_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            name="fk_usage_rollups_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_usage_rollups_workspace_id_workspaces",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["price_book_id"],
            ["price_books.id"],
            name="fk_usage_rollups_price_book_id_price_books",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "granularity IN ('HOUR', 'DAY', 'MONTH')",
            name="ck_usage_rollups_granularity_known",
        ),
        sa.CheckConstraint(
            "grain IN ('DETAIL', 'ORG_TOTAL')", name="ck_usage_rollups_grain_known"
        ),
        sa.CheckConstraint(
            "bucket_end > bucket_start", name="ck_usage_rollups_bucket_ordered"
        ),
        sa.CheckConstraint(
            "quantity >= 0", name="ck_usage_rollups_quantity_non_negative"
        ),
        sa.CheckConstraint(
            "cost_micros >= 0", name="ck_usage_rollups_cost_non_negative"
        ),
        sa.CheckConstraint(
            "event_count >= 0", name="ck_usage_rollups_event_count_non_negative"
        ),
        sa.CheckConstraint(
            "estimated_quantity >= 0",
            name="ck_usage_rollups_estimated_quantity_non_negative",
        ),
        sa.CheckConstraint(
            "estimated_quantity <= quantity",
            name="ck_usage_rollups_estimated_within_total",
        ),
        sa.CheckConstraint(
            "late_event_count >= 0", name="ck_usage_rollups_late_count_non_negative"
        ),
        sa.CheckConstraint(
            "grain <> 'ORG_TOTAL' OR ("
            " workspace_id IS NULL AND provider IS NULL AND model IS NULL"
            " AND price_book_id IS NULL AND unit_price_micros IS NULL)",
            name="ck_usage_rollups_org_total_has_no_dimensions",
        ),
        sa.CheckConstraint(
            "event_type <> '*' OR grain = 'ORG_TOTAL'",
            name="ck_usage_rollups_wildcard_is_org_total_only",
        ),
    )

    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_usage_rollups_bucket ON usage_rollups (
            organization_id,
            COALESCE(workspace_id, '{NIL_UUID}'::uuid),
            event_type,
            COALESCE(provider, ''),
            COALESCE(model, ''),
            COALESCE(price_book_id, '{NIL_UUID}'::uuid),
            grain,
            granularity,
            bucket_start
        )
        """
    )

    op.execute(
        "CREATE INDEX ix_usage_rollups_spend "
        "ON usage_rollups (organization_id, event_type, granularity, bucket_start) "
        "WHERE grain = 'ORG_TOTAL'"
    )
    op.create_index(
        "ix_usage_rollups_org_bucket",
        "usage_rollups",
        ["organization_id", "granularity", "bucket_start"],
    )
    op.execute(
        "CREATE INDEX ix_usage_rollups_workspace "
        "ON usage_rollups (workspace_id, granularity, bucket_start) "
        "WHERE workspace_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_usage_rollups_unsealed "
        "ON usage_rollups (granularity, bucket_start) "
        "WHERE sealed_at IS NULL"
    )

    op.create_table(
        "rollup_windows",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("granularity", sa.String(length=8), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'OPEN'"),
        ),
        sa.Column("first_rolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "event_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "late_event_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.CheckConstraint(
            "granularity IN ('HOUR', 'DAY', 'MONTH')",
            name="ck_rollup_windows_granularity_known",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'SEALED')", name="ck_rollup_windows_status_known"
        ),
        sa.CheckConstraint(
            "bucket_end > bucket_start", name="ck_rollup_windows_bucket_ordered"
        ),
        sa.CheckConstraint(
            "(status = 'SEALED') = (sealed_at IS NOT NULL)",
            name="ck_rollup_windows_sealed_at_matches_status",
        ),
    )

    op.create_index(
        "uq_rollup_windows_period",
        "rollup_windows",
        ["granularity", "bucket_start"],
        unique=True,
    )
    op.execute(
        "CREATE INDEX ix_rollup_windows_sealable "
        "ON rollup_windows (bucket_end) WHERE status = 'OPEN'"
    )

    op.execute(SEAL_IMMUTABILITY_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_usage_rollups_seal_immutable
        BEFORE UPDATE OR DELETE ON usage_rollups
        FOR EACH ROW EXECUTE FUNCTION usage_rollups_seal_immutable();
        """
    )

    op.execute(WINDOW_SEAL_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_rollup_windows_seal_immutable
        BEFORE UPDATE OR DELETE ON rollup_windows
        FOR EACH ROW EXECUTE FUNCTION rollup_windows_seal_immutable();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_rollup_windows_seal_immutable ON rollup_windows"
    )
    op.execute("DROP FUNCTION IF EXISTS rollup_windows_seal_immutable()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_usage_rollups_seal_immutable ON usage_rollups"
    )
    op.execute("DROP FUNCTION IF EXISTS usage_rollups_seal_immutable()")
    op.drop_table("rollup_windows")
    op.drop_table("usage_rollups")
