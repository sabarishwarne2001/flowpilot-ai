"""ARCH-14 Step 5 — reconciliation tables (EXPAND)

Revision ID: arch14_step5_reconciliation
Revises: arch14_step4_quota_tiers
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch14_step5_reconciliation"
down_revision = "arch14_step4_quota_tiers"
branch_labels = None
depends_on = None


STATEMENT_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION provider_statements_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        RAISE EXCEPTION
            'provider_statements % (% %) is imported evidence and cannot be '
            'deleted; import a corrected statement under a new source_key',
            OLD.id, OLD.provider, OLD.source_key
            USING ERRCODE = '42501';
    END IF;

    RAISE EXCEPTION
        'provider_statements % is immutable. A provider that restates a '
        'period publishes a new statement; overwriting the old one destroys '
        'the evidence that they changed it.',
        OLD.id
        USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;
"""


STATEMENT_LINE_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION provider_statement_lines_immutable()
RETURNS TRIGGER AS $$
DECLARE
    parent_exists BOOLEAN;
BEGIN
    IF (TG_OP = 'DELETE') THEN
        SELECT EXISTS (
            SELECT 1 FROM provider_statements WHERE id = OLD.provider_statement_id
        ) INTO parent_exists;
        IF parent_exists THEN
            RAISE EXCEPTION
                'provider_statement_lines % is imported evidence and cannot '
                'be deleted', OLD.id
                USING ERRCODE = '42501';
        END IF;
        RETURN OLD;
    END IF;

    RAISE EXCEPTION
        'provider_statement_lines % is immutable; it records what the '
        'provider wrote, not what we think they meant', OLD.id
        USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;
"""


RUN_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION reconciliation_runs_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        RAISE EXCEPTION
            'reconciliation_runs % is an audit record and cannot be deleted',
            OLD.id
            USING ERRCODE = '42501';
    END IF;

    IF OLD.status <> 'RUNNING' THEN
        RAISE EXCEPTION
            'reconciliation_runs % is already % and is immutable; re-run the '
            'period to produce a new record rather than editing this one',
            OLD.id, OLD.status
            USING ERRCODE = '42501';
    END IF;

    IF NEW.status = 'RUNNING' THEN
        RAISE EXCEPTION
            'reconciliation_runs % must leave RUNNING on update', OLD.id
            USING ERRCODE = '42501';
    END IF;

    IF (
        NEW.id                       IS DISTINCT FROM OLD.id
        OR NEW.provider              IS DISTINCT FROM OLD.provider
        OR NEW.period_start          IS DISTINCT FROM OLD.period_start
        OR NEW.period_end            IS DISTINCT FROM OLD.period_end
        OR NEW.started_at            IS DISTINCT FROM OLD.started_at
        OR NEW.created_at            IS DISTINCT FROM OLD.created_at
    ) THEN
        RAISE EXCEPTION
            'reconciliation_runs %: identity and period are fixed at start',
            OLD.id
            USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


FINDING_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION reconciliation_findings_immutable()
RETURNS TRIGGER AS $$
DECLARE
    parent_exists BOOLEAN;
BEGIN
    IF (TG_OP = 'DELETE') THEN
        SELECT EXISTS (
            SELECT 1 FROM reconciliation_runs WHERE id = OLD.reconciliation_run_id
        ) INTO parent_exists;
        IF parent_exists THEN
            RAISE EXCEPTION
                'reconciliation_findings % is an audit record and cannot be '
                'deleted', OLD.id
                USING ERRCODE = '42501';
        END IF;
        RETURN OLD;
    END IF;

    RAISE EXCEPTION
        'reconciliation_findings % is immutable. Tidying a stale UNEXPLAINED '
        'residue destroys the only record of how large it was.', OLD.id
        USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # ------------------------------------------------------------------
    # provider_statements
    # ------------------------------------------------------------------
    op.create_table(
        "provider_statements",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("source_key", sa.String(length=200), nullable=False),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.Column("source_digest", sa.String(length=64), nullable=True),
        sa.Column("grain", sa.String(length=16), nullable=False),
        sa.Column("attribution", sa.String(length=16), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default=sa.text("'USD'"),
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("imported_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "line_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "total_cost_micros",
            sa.BigInteger(),
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
        sa.ForeignKeyConstraint(
            ["imported_by_user_id"],
            ["users.id"],
            name="fk_provider_statements_imported_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "period_end > period_start", name="ck_provider_statements_period_ordered"
        ),
        sa.CheckConstraint(
            "length(provider) > 0", name="ck_provider_statements_provider_not_blank"
        ),
        sa.CheckConstraint(
            "length(currency) = 3", name="ck_provider_statements_currency_iso4217"
        ),
        sa.CheckConstraint(
            "grain IN ('DAY', 'MONTH', 'INVOICE')",
            name="ck_provider_statements_grain_known",
        ),
        sa.CheckConstraint(
            "attribution IN ('ATTESTED', 'ALLOCATED', 'AGGREGATE')",
            name="ck_provider_statements_attribution_known",
        ),
        sa.CheckConstraint(
            "line_count >= 0", name="ck_provider_statements_line_count_non_negative"
        ),
    )
    op.create_index(
        "uq_provider_statements_source",
        "provider_statements",
        ["provider", "source_key"],
        unique=True,
    )
    op.create_index(
        "ix_provider_statements_period",
        "provider_statements",
        ["provider", "period_start", "period_end"],
    )

    # ------------------------------------------------------------------
    # provider_statement_lines
    # ------------------------------------------------------------------
    op.create_table(
        "provider_statement_lines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "provider_statement_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("sku", sa.String(length=200), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_on", sa.Date(), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=30, scale=6), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default=sa.text("'USD'"),
        ),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            ["provider_statement_id"],
            ["provider_statements.id"],
            name="fk_provider_statement_lines_statement_id_provider_statements",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_provider_statement_lines_organization_id_organizations",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "cost_micros >= 0", name="ck_provider_statement_lines_cost_non_negative"
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity >= 0",
            name="ck_provider_statement_lines_quantity_non_negative",
        ),
    )
    op.create_index(
        "ix_provider_statement_lines_statement",
        "provider_statement_lines",
        ["provider_statement_id", "model"],
    )
    op.execute(
        "CREATE INDEX ix_provider_statement_lines_org "
        "ON provider_statement_lines (organization_id) "
        "WHERE organization_id IS NOT NULL"
    )

    # ------------------------------------------------------------------
    # reconciliation_runs
    # ------------------------------------------------------------------
    op.create_table(
        "reconciliation_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "provider_statement_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("grain", sa.String(length=16), nullable=True),
        sa.Column(
            "attribution",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'AGGREGATE'"),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'RUNNING'"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ledger_cost_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "statement_cost_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "drift_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "drift_bps",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "findings_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "alert_raised",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
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
        sa.ForeignKeyConstraint(
            ["provider_statement_id"],
            ["provider_statements.id"],
            name="fk_reconciliation_runs_statement_id_provider_statements",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "period_end > period_start", name="ck_reconciliation_runs_period_ordered"
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'FAILED', 'REFUSED')",
            name="ck_reconciliation_runs_status_known",
        ),
        sa.CheckConstraint(
            "attribution IN ('ATTESTED', 'ALLOCATED', 'AGGREGATE')",
            name="ck_reconciliation_runs_attribution_known",
        ),
        sa.CheckConstraint(
            "findings_count >= 0", name="ck_reconciliation_runs_findings_non_negative"
        ),
    )
    op.execute(
        "CREATE INDEX ix_reconciliation_runs_period "
        "ON reconciliation_runs (provider, period_start, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_reconciliation_runs_alerts "
        "ON reconciliation_runs (started_at DESC) WHERE alert_raised"
    )

    # ------------------------------------------------------------------
    # reconciliation_findings
    # ------------------------------------------------------------------
    op.create_table(
        "reconciliation_findings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "reconciliation_run_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("attribution", sa.String(length=16), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=True),
        sa.Column("ledger_quantity", sa.Numeric(precision=30, scale=6), nullable=True),
        sa.Column(
            "statement_quantity", sa.Numeric(precision=30, scale=6), nullable=True
        ),
        sa.Column(
            "ledger_cost_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "statement_cost_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("drift_micros", sa.BigInteger(), nullable=False),
        sa.Column(
            "drift_bps",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("explanation", sa.Text(), nullable=False),
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
            ["reconciliation_run_id"],
            ["reconciliation_runs.id"],
            name="fk_reconciliation_findings_run_id_reconciliation_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_reconciliation_findings_organization_id_organizations",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "category IN ('TIMING_BOUNDARY', 'ESTIMATE_DRIFT', 'PRICE_DRIFT', "
            "'UNMETERED_GENERATION', 'OVERMETERED_LEDGER', 'UNEXPLAINED')",
            name="ck_reconciliation_findings_category_known",
        ),
        sa.CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'HIGH', 'CRITICAL')",
            name="ck_reconciliation_findings_severity_known",
        ),
        sa.CheckConstraint(
            "attribution IN ('ATTESTED', 'ALLOCATED', 'AGGREGATE')",
            name="ck_reconciliation_findings_attribution_known",
        ),
        sa.CheckConstraint(
            "organization_id IS NULL OR attribution = 'ATTESTED'",
            name="ck_reconciliation_findings_org_findings_require_attested",
        ),
        sa.CheckConstraint(
            "category <> 'UNMETERED_GENERATION' OR severity = 'CRITICAL'",
            name="ck_reconciliation_findings_unmetered_is_always_critical",
        ),
    )
    op.create_index(
        "ix_reconciliation_findings_run",
        "reconciliation_findings",
        ["reconciliation_run_id", "category"],
    )
    op.execute(
        "CREATE INDEX ix_reconciliation_findings_critical "
        "ON reconciliation_findings (created_at DESC) "
        "WHERE severity = 'CRITICAL'"
    )

    # ------------------------------------------------------------------
    # Triggers
    # ------------------------------------------------------------------
    op.execute(STATEMENT_IMMUTABILITY_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_provider_statements_immutable
        BEFORE UPDATE OR DELETE ON provider_statements
        FOR EACH ROW EXECUTE FUNCTION provider_statements_immutable();
        """
    )

    op.execute(STATEMENT_LINE_IMMUTABILITY_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_provider_statement_lines_immutable
        BEFORE UPDATE OR DELETE ON provider_statement_lines
        FOR EACH ROW EXECUTE FUNCTION provider_statement_lines_immutable();
        """
    )

    op.execute(RUN_IMMUTABILITY_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_reconciliation_runs_immutable
        BEFORE UPDATE OR DELETE ON reconciliation_runs
        FOR EACH ROW EXECUTE FUNCTION reconciliation_runs_immutable();
        """
    )

    op.execute(FINDING_IMMUTABILITY_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_reconciliation_findings_immutable
        BEFORE UPDATE OR DELETE ON reconciliation_findings
        FOR EACH ROW EXECUTE FUNCTION reconciliation_findings_immutable();
        """
    )


def downgrade() -> None:
    for trigger, table in (
        ("trg_reconciliation_findings_immutable", "reconciliation_findings"),
        ("trg_reconciliation_runs_immutable", "reconciliation_runs"),
        ("trg_provider_statement_lines_immutable", "provider_statement_lines"),
        ("trg_provider_statements_immutable", "provider_statements"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    for function in (
        "reconciliation_findings_immutable",
        "reconciliation_runs_immutable",
        "provider_statement_lines_immutable",
        "provider_statements_immutable",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}()")

    op.drop_table("reconciliation_findings")
    op.drop_table("reconciliation_runs")
    op.drop_table("provider_statement_lines")
    op.drop_table("provider_statements")