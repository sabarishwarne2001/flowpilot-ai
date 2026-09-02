"""ARCH-17 Step 1 — SLO definitions, observations, measurements; jobs trace columns (EXPAND)

Revision ID: arch17_step1_slos
Revises: arch16_step8_outbox_identity_vocabulary
Create Date: 2026-08-30

Pure EXPAND. Three new tables, three new enum types, one SQL helper function,
and two nullable columns on `jobs`. Nothing existing is rewritten and no
backfill is needed: a job enqueued before this migration simply has NULL
`trace_id`, which `job_scope` reports as `trace_origin=ORPHAN` rather than
treating as an error.

The `jobs` columns are the correction that matters. The ARCH-17 kickoff recorded
the `jobs` table as "ready for trace_id / correlation_id propagation"; it was
not — `app/models/job.py` has neither column, and a trace therefore died at the
queue boundary. They are added here.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch17_step1_slos"
down_revision = "arch16_step8_outbox_identity_vocabulary"
branch_labels = None
depends_on = None


# Elementwise addition of two histogram arrays, refusing when the two rows were
# bucketed against different boundary schedules. A plain elementwise add would
# silently combine incomparable histograms after any change to
# DEFAULT_LATENCY_BOUNDS_MS, producing a distribution that never existed. The
# guard makes the mismatch a no-op (the stored row wins) rather than a
# corruption, so at worst one deploy window's observations are under-counted and
# the numbers stay honest.
BUCKET_ADD_FUNCTION = """
CREATE OR REPLACE FUNCTION slo_add_bucket_counts(
    stored_counts  jsonb,
    stored_bounds  jsonb,
    incoming_counts jsonb,
    incoming_bounds jsonb
)
RETURNS jsonb AS $$
BEGIN
    IF stored_bounds IS DISTINCT FROM incoming_bounds THEN
        RAISE WARNING
            'slo_add_bucket_counts: bucket schedule changed; keeping stored '
            'histogram and discarding % incoming counts',
            jsonb_array_length(COALESCE(incoming_counts, '[]'::jsonb));
        RETURN stored_counts;
    END IF;

    IF stored_counts IS NULL OR jsonb_array_length(stored_counts) = 0 THEN
        RETURN COALESCE(incoming_counts, '[]'::jsonb);
    END IF;
    IF incoming_counts IS NULL OR jsonb_array_length(incoming_counts) = 0 THEN
        RETURN stored_counts;
    END IF;
    IF jsonb_array_length(stored_counts) <> jsonb_array_length(incoming_counts) THEN
        RAISE WARNING 'slo_add_bucket_counts: length mismatch; keeping stored';
        RETURN stored_counts;
    END IF;

    RETURN (
        SELECT COALESCE(jsonb_agg(total ORDER BY position), '[]'::jsonb)
        FROM (
            SELECT
                position,
                (stored_counts   -> (position - 1))::text::bigint
              + (incoming_counts -> (position - 1))::text::bigint AS total
            FROM generate_series(1, jsonb_array_length(stored_counts)) AS position
        ) AS summed
    );
END;
$$ LANGUAGE plpgsql IMMUTABLE;
"""


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Enum types
    # ------------------------------------------------------------------
    op.execute("CREATE TYPE slo_unit AS ENUM ('MILLISECONDS', 'RATIO')")
    op.execute("CREATE TYPE slo_window AS ENUM ('HOUR', 'DAY', 'MONTH')")
    op.execute(
        "CREATE TYPE slo_method AS ENUM ('EXACT', 'HISTOGRAM_INTERPOLATED')"
    )

    # ------------------------------------------------------------------
    # slo_definitions
    # ------------------------------------------------------------------
    op.create_table(
        "slo_definitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("slo_key", sa.String(length=100), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_value", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column(
            "unit",
            postgresql.ENUM("MILLISECONDS", "RATIO", name="slo_unit", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "window_period",
            postgresql.ENUM("HOUR", "DAY", "MONTH", name="slo_window", create_type=False),
            nullable=False,
            server_default=sa.text("'DAY'::slo_window"),
        ),
        sa.Column(
            "is_contractual",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("display_name", sa.String(length=150), nullable=True),
        sa.Column("notes", sa.String(length=1000), nullable=True),
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
            name="fk_slo_definitions_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "length(slo_key) > 0", name="ck_slo_definitions_slo_key_not_blank"
        ),
        sa.CheckConstraint(
            "target_value >= 0", name="ck_slo_definitions_target_non_negative"
        ),
        sa.CheckConstraint(
            "unit <> 'RATIO'::slo_unit OR target_value <= 1",
            name="ck_slo_definitions_ratio_target_is_a_proportion",
        ),
        sa.CheckConstraint(
            "NOT is_contractual OR organization_id IS NOT NULL",
            name="ck_slo_definitions_contractual_requires_tenant",
        ),
    )

    op.execute(
        "CREATE UNIQUE INDEX uq_slo_definitions_tenant_key "
        "ON slo_definitions (slo_key, organization_id) "
        "WHERE organization_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_slo_definitions_platform_key "
        "ON slo_definitions (slo_key) "
        "WHERE organization_id IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_slo_definitions_organization_id "
        "ON slo_definitions (organization_id) "
        "WHERE organization_id IS NOT NULL"
    )

    # ------------------------------------------------------------------
    # slo_observations
    # ------------------------------------------------------------------
    op.create_table(
        "slo_observations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slo_key", sa.String(length=100), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "sample_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "error_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "sum_value",
            sa.Numeric(precision=20, scale=4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "bucket_bounds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "bucket_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
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
            name="fk_slo_observations_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "sample_count >= 0", name="ck_slo_observations_sample_count_non_negative"
        ),
        sa.CheckConstraint(
            "error_count >= 0", name="ck_slo_observations_error_count_non_negative"
        ),
        sa.CheckConstraint(
            "error_count <= sample_count",
            name="ck_slo_observations_errors_within_samples",
        ),
        sa.CheckConstraint(
            "sum_value >= 0", name="ck_slo_observations_sum_non_negative"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(bucket_counts) = 'array'",
            name="ck_slo_observations_buckets_are_an_array",
        ),
        sa.CheckConstraint(
            "date_trunc('hour', window_start) = window_start",
            name="ck_slo_observations_window_start_is_an_hour",
        ),
    )

    op.create_index(
        "uq_slo_observations_scope",
        "slo_observations",
        ["organization_id", "slo_key", "window_start"],
        unique=True,
    )
    op.create_index(
        "ix_slo_observations_key_window",
        "slo_observations",
        ["slo_key", "window_start"],
    )
    op.create_index(
        "ix_slo_observations_window_start", "slo_observations", ["window_start"]
    )

    op.execute(BUCKET_ADD_FUNCTION)

    # ------------------------------------------------------------------
    # slo_measurements
    # ------------------------------------------------------------------
    op.create_table(
        "slo_measurements",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("slo_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slo_key", sa.String(length=100), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_value", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("target_value", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column(
            "unit",
            postgresql.ENUM("MILLISECONDS", "RATIO", name="slo_unit", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "method",
            postgresql.ENUM(
                "EXACT",
                "HISTOGRAM_INTERPOLATED",
                name="slo_method",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'HISTOGRAM_INTERPOLATED'::slo_method"),
        ),
        sa.Column(
            "sample_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "error_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "breached", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "is_contractual",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
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
            ["slo_definition_id"],
            ["slo_definitions.id"],
            name="fk_slo_measurements_slo_definition_id_slo_definitions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_slo_measurements_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "sample_count >= 0", name="ck_slo_measurements_sample_count_non_negative"
        ),
        sa.CheckConstraint(
            "window_end > window_start", name="ck_slo_measurements_window_ordered"
        ),
        sa.CheckConstraint(
            "observed_value >= 0", name="ck_slo_measurements_observed_non_negative"
        ),
        sa.CheckConstraint(
            "sample_count > 0 OR NOT breached",
            name="ck_slo_measurements_empty_window_cannot_breach",
        ),
    )

    op.create_index(
        "uq_slo_measurements_scope",
        "slo_measurements",
        ["slo_definition_id", "organization_id", "window_start"],
        unique=True,
    )
    op.execute(
        "CREATE INDEX ix_slo_measurements_org_window "
        "ON slo_measurements (organization_id, window_start DESC)"
    )
    op.execute(
        "CREATE INDEX ix_slo_measurements_breaches "
        "ON slo_measurements (organization_id, window_start DESC) "
        "WHERE breached"
    )

    # ------------------------------------------------------------------
    # jobs: the queue-boundary trace columns
    # ------------------------------------------------------------------
    op.add_column(
        "jobs", sa.Column("trace_id", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "jobs",
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_jobs_trace_id_is_w3c_hex",
        "jobs",
        "trace_id IS NULL OR trace_id ~ '^[0-9a-f]{32}$'",
    )
    op.execute(
        "CREATE INDEX ix_jobs_trace_id ON jobs (trace_id) "
        "WHERE trace_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_jobs_correlation_id ON jobs (correlation_id) "
        "WHERE correlation_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_jobs_correlation_id")
    op.execute("DROP INDEX IF EXISTS ix_jobs_trace_id")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_trace_id_is_w3c_hex")
    op.drop_column("jobs", "correlation_id")
    op.drop_column("jobs", "trace_id")

    op.execute("DROP INDEX IF EXISTS ix_slo_measurements_breaches")
    op.execute("DROP INDEX IF EXISTS ix_slo_measurements_org_window")
    op.drop_table("slo_measurements")

    op.execute(
        "DROP FUNCTION IF EXISTS slo_add_bucket_counts(jsonb, jsonb, jsonb, jsonb)"
    )
    op.drop_table("slo_observations")

    op.execute("DROP INDEX IF EXISTS ix_slo_definitions_organization_id")
    op.execute("DROP INDEX IF EXISTS uq_slo_definitions_platform_key")
    op.execute("DROP INDEX IF EXISTS uq_slo_definitions_tenant_key")
    op.drop_table("slo_definitions")

    op.execute("DROP TYPE IF EXISTS slo_method")
    op.execute("DROP TYPE IF EXISTS slo_window")
    op.execute("DROP TYPE IF EXISTS slo_unit")
