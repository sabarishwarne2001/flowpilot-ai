"""ARCH-18 Step 1 — cost basis, supplier invoices, reconciliation (EXPAND)

Revision ID: arch18_step1_cogs_margins
Revises: arch17_step1_slos
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch18_step1_cogs_margins"
down_revision = "arch17_step1_slos"
branch_labels = None
depends_on = None

COST_BASIS_SOURCE_VALUES: tuple[str, ...] = (
    "SUPPLIER_RATE_CARD",
    "MEASURED",
    "ESTIMATED",
    "ZERO_BYOK",
)

RECONCILIATION_STATUS_VALUES: tuple[str, ...] = (
    "MATCHED",
    "INVESTIGATE",
    "ACCEPTED",
)

_SOURCE_SQL_LIST = ", ".join(f"'{v}'" for v in COST_BASIS_SOURCE_VALUES)
_STATUS_SQL_LIST = ", ".join(f"'{v}'" for v in RECONCILIATION_STATUS_VALUES)

IMMUTABILITY_FUNCTION_V3 = """
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
        OR NEW.cost_basis_micros IS DISTINCT FROM OLD.cost_basis_micros
        OR NEW.cost_basis_source IS DISTINCT FROM OLD.cost_basis_source
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

SUPPLIER_INVOICE_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION supplier_invoices_amount_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM supplier_reconciliations r
         WHERE r.supplier_invoice_id = OLD.id
    ) THEN
        IF (
            NEW.provider              IS DISTINCT FROM OLD.provider
            OR NEW.period_start       IS DISTINCT FROM OLD.period_start
            OR NEW.period_end         IS DISTINCT FROM OLD.period_end
            OR NEW.invoiced_total_micros IS DISTINCT FROM OLD.invoiced_total_micros
            OR NEW.currency           IS DISTINCT FROM OLD.currency
        ) THEN
            RAISE EXCEPTION
                'supplier_invoices row % has been reconciled; its provider, '
                'period, total and currency are frozen. Ingest a corrected '
                'invoice and reconcile again rather than editing this one.',
                OLD.id
                USING ERRCODE = '42501';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # 1. price_book_entries
    op.add_column(
        "price_book_entries",
        sa.Column(
            "cost_basis_micros",
            sa.Numeric(precision=20, scale=9),
            nullable=True,
            comment="What the supplier charges us per unit, in micros. NULL means unknown, never free.",
        ),
    )
    op.add_column(
        "price_book_entries",
        sa.Column(
            "cost_basis_source",
            sa.String(length=24),
            nullable=True,
            comment="SUPPLIER_RATE_CARD | MEASURED | ESTIMATED | ZERO_BYOK",
        ),
    )

    op.execute(
        """
        ALTER TABLE price_book_entries
        ADD CONSTRAINT ck_price_book_entries_cost_basis_pair_complete
        CHECK ((cost_basis_micros IS NULL) = (cost_basis_source IS NULL))
        """
    )
    op.execute(
        """
        ALTER TABLE price_book_entries
        ADD CONSTRAINT ck_price_book_entries_cost_basis_non_negative
        CHECK (cost_basis_micros IS NULL OR cost_basis_micros >= 0)
        """
    )
    op.execute(
        f"""
        ALTER TABLE price_book_entries
        ADD CONSTRAINT ck_price_book_entries_cost_basis_source_known
        CHECK (cost_basis_source IS NULL
               OR cost_basis_source IN ({_SOURCE_SQL_LIST}))
        """
    )
    op.execute(
        """
        ALTER TABLE price_book_entries
        ADD CONSTRAINT ck_price_book_entries_zero_cost_is_declared
        CHECK (cost_basis_micros IS NULL
               OR (cost_basis_micros = 0) = (cost_basis_source = 'ZERO_BYOK'))
        """
    )

    # 2. usage_events
    op.add_column(
        "usage_events",
        sa.Column(
            "cost_basis_micros",
            sa.BigInteger(),
            nullable=True,
            comment="Supplier cost for this row's quantity, captured at settle time. NULL = unknown.",
        ),
    )
    op.add_column(
        "usage_events",
        sa.Column(
            "cost_basis_source",
            sa.String(length=24),
            nullable=True,
            comment="Provenance of cost_basis_micros. NULL iff the cost is unknown.",
        ),
    )

    op.execute(
        """
        ALTER TABLE usage_events
        ADD CONSTRAINT ck_usage_events_cost_basis_pair_complete
        CHECK ((cost_basis_micros IS NULL) = (cost_basis_source IS NULL))
        """
    )
    op.execute(
        """
        ALTER TABLE usage_events
        ADD CONSTRAINT ck_usage_events_cost_basis_non_negative
        CHECK (cost_basis_micros IS NULL OR cost_basis_micros >= 0)
        """
    )
    op.execute(
        f"""
        ALTER TABLE usage_events
        ADD CONSTRAINT ck_usage_events_cost_basis_source_known
        CHECK (cost_basis_source IS NULL
               OR cost_basis_source IN ({_SOURCE_SQL_LIST}))
        """
    )
    op.execute(
        """
        ALTER TABLE usage_events
        ADD CONSTRAINT ck_usage_events_zero_cost_is_declared
        CHECK (cost_basis_micros IS NULL
               OR (cost_basis_micros = 0) = (cost_basis_source = 'ZERO_BYOK'))
        """
    )

    op.create_index(
        "ix_usage_events_provider_cost_basis",
        "usage_events",
        ["provider", "occurred_at"],
        unique=False,
        postgresql_where=sa.text("provider IS NOT NULL"),
    )
    op.create_index(
        "ix_usage_events_unknown_cost_basis",
        "usage_events",
        ["occurred_at"],
        unique=False,
        postgresql_where=sa.text("cost_basis_micros IS NULL"),
    )

    op.execute(IMMUTABILITY_FUNCTION_V3)

    # 3. supplier_invoices
    op.create_table(
        "supplier_invoices",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column(
            "invoice_reference",
            sa.String(length=200),
            nullable=True,
            comment="The supplier's own invoice number.",
        ),
        sa.Column(
            "period_start",
            sa.Date(),
            nullable=False,
            comment="First day covered, inclusive.",
        ),
        sa.Column(
            "period_end",
            sa.Date(),
            nullable=False,
            comment="Last day covered, INCLUSIVE.",
        ),
        sa.Column("invoiced_total_micros", sa.BigInteger(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default=sa.text("'USD'"),
        ),
        sa.Column(
            "raw_document_file_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Platform-scoped invoice PDF (organization_id NULL).",
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "ingested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["raw_document_file_id"],
            ["uploaded_files.id"],
            name="fk_supplier_invoices_raw_document_file_id_uploaded_files",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingested_by_user_id"],
            ["users.id"],
            name="fk_supplier_invoices_ingested_by_user_id_users",
            ondelete="SET NULL",
        ),
    )

    op.execute(
        """
        ALTER TABLE supplier_invoices
        ADD CONSTRAINT ck_supplier_invoices_period_ordered
        CHECK (period_end >= period_start)
        """
    )
    op.execute(
        """
        ALTER TABLE supplier_invoices
        ADD CONSTRAINT ck_supplier_invoices_total_non_negative
        CHECK (invoiced_total_micros >= 0)
        """
    )
    op.execute(
        """
        ALTER TABLE supplier_invoices
        ADD CONSTRAINT ck_supplier_invoices_currency_iso4217
        CHECK (length(currency) = 3)
        """
    )
    op.execute(
        """
        ALTER TABLE supplier_invoices
        ADD CONSTRAINT ck_supplier_invoices_provider_not_blank
        CHECK (length(provider) > 0)
        """
    )
    op.create_index(
        "uq_supplier_invoices_provider_period",
        "supplier_invoices",
        ["provider", "period_start", "period_end"],
        unique=True,
    )
    op.create_index(
        "ix_supplier_invoices_period_start",
        "supplier_invoices",
        ["period_start"],
        unique=False,
    )

    # 4. supplier_reconciliations
    op.create_table(
        "supplier_reconciliations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "supplier_invoice_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "modelled_total_micros",
            sa.BigInteger(),
            nullable=False,
            comment="SUM(usage_events.cost_basis_micros) over the window.",
        ),
        sa.Column(
            "variance_micros",
            sa.BigInteger(),
            nullable=False,
            comment="invoiced - modelled.",
        ),
        sa.Column(
            "variance_ratio",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
            comment="variance / modelled. NULL when modelled is 0.",
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "modelled_event_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "unknown_cost_event_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "reconciled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "reconciled_by_user_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["supplier_invoice_id"],
            ["supplier_invoices.id"],
            name="fk_supplier_reconciliations_invoice",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reconciled_by_user_id"],
            ["users.id"],
            name="fk_supplier_reconciliations_reconciled_by_user_id_users",
            ondelete="SET NULL",
        ),
    )

    op.execute(
        f"""
        ALTER TABLE supplier_reconciliations
        ADD CONSTRAINT ck_supplier_reconciliations_status_known
        CHECK (status IN ({_STATUS_SQL_LIST}))
        """
    )
    op.execute(
        """
        ALTER TABLE supplier_reconciliations
        ADD CONSTRAINT ck_supplier_reconciliations_modelled_non_negative
        CHECK (modelled_total_micros >= 0)
        """
    )
    op.execute(
        """
        ALTER TABLE supplier_reconciliations
        ADD CONSTRAINT ck_supplier_reconciliations_ratio_iff_modelled
        CHECK ((modelled_total_micros = 0) = (variance_ratio IS NULL))
        """
    )
    op.create_index(
        "ix_supplier_reconciliations_invoice",
        "supplier_reconciliations",
        ["supplier_invoice_id", "reconciled_at"],
        unique=False,
    )

    op.execute(SUPPLIER_INVOICE_IMMUTABILITY_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_supplier_invoices_amount_immutable
        BEFORE UPDATE ON supplier_invoices
        FOR EACH ROW EXECUTE FUNCTION supplier_invoices_amount_immutable();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_supplier_invoices_amount_immutable "
        "ON supplier_invoices"
    )
    op.execute("DROP FUNCTION IF EXISTS supplier_invoices_amount_immutable()")

    op.drop_index(
        "ix_supplier_reconciliations_invoice", table_name="supplier_reconciliations"
    )
    op.drop_table("supplier_reconciliations")

    op.drop_index("ix_supplier_invoices_period_start", table_name="supplier_invoices")
    op.drop_index("uq_supplier_invoices_provider_period", table_name="supplier_invoices")
    op.drop_table("supplier_invoices")

    op.execute(IMMUTABILITY_FUNCTION_V2)

    op.drop_index("ix_usage_events_unknown_cost_basis", table_name="usage_events")
    op.drop_index("ix_usage_events_provider_cost_basis", table_name="usage_events")

    for name in (
        "ck_usage_events_zero_cost_is_declared",
        "ck_usage_events_cost_basis_source_known",
        "ck_usage_events_cost_basis_non_negative",
        "ck_usage_events_cost_basis_pair_complete",
    ):
        op.execute(f"ALTER TABLE usage_events DROP CONSTRAINT IF EXISTS {name}")

    op.drop_column("usage_events", "cost_basis_source")
    op.drop_column("usage_events", "cost_basis_micros")

    for name in (
        "ck_price_book_entries_zero_cost_is_declared",
        "ck_price_book_entries_cost_basis_source_known",
        "ck_price_book_entries_cost_basis_non_negative",
        "ck_price_book_entries_cost_basis_pair_complete",
    ):
        op.execute(f"ALTER TABLE price_book_entries DROP CONSTRAINT IF EXISTS {name}")

    op.drop_column("price_book_entries", "cost_basis_source")
    op.drop_column("price_book_entries", "cost_basis_micros")