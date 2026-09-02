"""ARCH-15 Step 15.5 — invoices and invoice_line_items (EXPAND)

The A9 tranche. An invoice that cannot be reproduced is a liability that grows
with every month of revenue, and it is the one thing in this phase that is
genuinely expensive to retrofit: the data you would need was never written
down.

Revision ID: arch15_step5_invoices
Revises: arch15_step4_seat_event_vocabulary
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch15_step5_invoices"
down_revision = "arch15_step4_seat_event_vocabulary"
branch_labels = None
depends_on = None


INVOICE_STATUS_ENUM = "invoice_status"
INVOICE_LINE_KIND_ENUM = "invoice_line_kind"

INVOICE_STATUS_VALUES: tuple[str, ...] = (
    "DRAFT",
    "OPEN",
    "PAID",
    "VOID",
    "UNCOLLECTIBLE",
)

INVOICE_LINE_KIND_VALUES: tuple[str, ...] = (
    "SEAT",
    "INCLUDED",
    "OVERAGE",
    "CREDIT",
    "TAX",
)


# ---------------------------------------------------------------------------
# The immutability trigger
# ---------------------------------------------------------------------------
INVOICE_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION invoices_finalized_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.finalized_at IS NULL THEN
        RETURN NEW;
    END IF;

    IF NEW.number              IS DISTINCT FROM OLD.number
    OR NEW.currency            IS DISTINCT FROM OLD.currency
    OR NEW.period_start        IS DISTINCT FROM OLD.period_start
    OR NEW.period_end          IS DISTINCT FROM OLD.period_end
    OR NEW.subtotal_micros     IS DISTINCT FROM OLD.subtotal_micros
    OR NEW.tax_micros          IS DISTINCT FROM OLD.tax_micros
    OR NEW.total_micros        IS DISTINCT FROM OLD.total_micros
    OR NEW.price_book_id       IS DISTINCT FROM OLD.price_book_id
    OR NEW.quota_tier_id       IS DISTINCT FROM OLD.quota_tier_id
    OR NEW.seats_billed        IS DISTINCT FROM OLD.seats_billed
    OR NEW.content_digest      IS DISTINCT FROM OLD.content_digest
    OR NEW.billing_account_id  IS DISTINCT FROM OLD.billing_account_id
    OR NEW.subscription_id     IS DISTINCT FROM OLD.subscription_id
    OR NEW.finalized_at        IS DISTINCT FROM OLD.finalized_at
    THEN
        RAISE EXCEPTION
            'invoice % is finalized; % is frozen. Issue a credit note instead '
            '(ARCH-15 A9: a finalized invoice must reproduce byte-identically '
            'eleven months later)', OLD.number, 'this column'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

LINE_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION invoice_line_items_finalized_immutable()
RETURNS TRIGGER AS $$
DECLARE
    parent_finalized timestamptz;
    parent_number    text;
    target_invoice   uuid;
BEGIN
    target_invoice := COALESCE(NEW.invoice_id, OLD.invoice_id);

    SELECT i.finalized_at, i.number
      INTO parent_finalized, parent_number
      FROM invoices i
     WHERE i.id = target_invoice;

    -- The parent row is gone: this is the CASCADE from a DELETE of a draft,
    -- or of an invoice a downgrade is removing. Nothing to protect.
    IF NOT FOUND THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    IF parent_finalized IS NOT NULL THEN
        RAISE EXCEPTION
            'invoice % is finalized; its line items are frozen',
            parent_number
            USING ERRCODE = '23514';
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    postgresql.ENUM(*INVOICE_STATUS_VALUES, name=INVOICE_STATUS_ENUM).create(
        op.get_bind(), checkfirst=True
    )
    postgresql.ENUM(*INVOICE_LINE_KIND_VALUES, name=INVOICE_LINE_KIND_ENUM).create(
        op.get_bind(), checkfirst=True
    )

    op.execute("CREATE SEQUENCE IF NOT EXISTS invoice_number_seq AS bigint START 1")

    op.create_table(
        "invoices",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("billing_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("stripe_invoice_id", sa.Text(), nullable=True),
        sa.Column("number", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                *INVOICE_STATUS_VALUES, name=INVOICE_STATUS_ENUM, create_type=False
            ),
            nullable=False,
            server_default=sa.text(f"'DRAFT'::{INVOICE_STATUS_ENUM}"),
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "subtotal_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "tax_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "total_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "amount_paid_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # A9. The provenance triple, frozen.
        sa.Column("price_book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quota_tier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seats_billed", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assembly_notes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            ["billing_account_id"],
            ["billing_accounts.id"],
            name="fk_invoices_billing_account_id_billing_accounts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.id"],
            name="fk_invoices_subscription_id_subscriptions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["price_book_id"],
            ["price_books.id"],
            name="fk_invoices_price_book_id_price_books",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quota_tier_id"],
            ["quota_tiers.id"],
            name="fk_invoices_quota_tier_id_quota_tiers",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("number", name="uq_invoices_number"),
        sa.UniqueConstraint("stripe_invoice_id", name="uq_invoices_stripe_invoice_id"),
        sa.CheckConstraint(
            "total_micros = subtotal_micros + tax_micros",
            name="ck_invoices_total_is_subtotal_plus_tax",
        ),
        sa.CheckConstraint(
            "period_end > period_start", name="ck_invoices_period_ordered"
        ),
        sa.CheckConstraint(
            "finalized_at IS NULL OR content_digest <> ''",
            name="ck_invoices_finalized_has_digest",
        ),
        sa.CheckConstraint(
            "amount_paid_micros >= 0 AND amount_paid_micros <= total_micros",
            name="ck_invoices_paid_within_total",
        ),
        sa.CheckConstraint("seats_billed >= 0", name="ck_invoices_seats_non_negative"),
        sa.CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_invoices_currency_iso4217",
        ),
        sa.CheckConstraint(
            "content_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_invoices_digest_shape",
        ),
        sa.CheckConstraint(
            f"status <> 'PAID'::{INVOICE_STATUS_ENUM} OR paid_at IS NOT NULL",
            name="ck_invoices_paid_has_paid_at",
        ),
        sa.CheckConstraint(
            f"status = 'DRAFT'::{INVOICE_STATUS_ENUM} OR finalized_at IS NOT NULL",
            name="ck_invoices_non_draft_is_finalized",
        ),
    )

    op.create_index(
        "ix_invoices_account_period",
        "invoices",
        ["billing_account_id", "period_start"],
    )
    op.execute(
        "CREATE INDEX ix_invoices_account_created "
        "ON invoices (billing_account_id, created_at DESC)"
    )
    op.create_index("ix_invoices_subscription_id", "invoices", ["subscription_id"])
    op.create_index("ix_invoices_price_book_id", "invoices", ["price_book_id"])
    op.create_index("ix_invoices_quota_tier_id", "invoices", ["quota_tier_id"])
    op.execute(
        "CREATE INDEX ix_invoices_open "
        "ON invoices (period_end DESC) "
        f"WHERE status = 'OPEN'::{INVOICE_STATUS_ENUM}"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_invoices_subscription_period "
        "ON invoices (subscription_id, period_start) "
        f"WHERE subscription_id IS NOT NULL "
        f"AND status <> 'VOID'::{INVOICE_STATUS_ENUM}"
    )

    op.create_table(
        "invoice_line_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(
                *INVOICE_LINE_KIND_VALUES,
                name=INVOICE_LINE_KIND_ENUM,
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("unit_price_micros", sa.Numeric(20, 6), nullable=False),
        sa.Column("amount_micros", sa.BigInteger(), nullable=False),
        sa.Column("price_book_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("usage_event_count", sa.Integer(), nullable=True),
        sa.Column("limit_key", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=True),
        sa.Column("included_quantity", sa.Numeric(20, 6), nullable=True),
        sa.Column("estimated_quantity", sa.Numeric(20, 6), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_invoice_line_items_invoice_id_invoices",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["price_book_entry_id"],
            ["price_book_entries.id"],
            name="fk_invoice_line_items_price_book_entry_id_price_book_entries",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "invoice_id", "line_number", name="uq_invoice_line_items_number"
        ),
        sa.CheckConstraint(
            "amount_micros = round(quantity * unit_price_micros)",
            name="ck_invoice_line_amount_matches",
        ),
        sa.CheckConstraint(
            "quantity >= 0", name="ck_invoice_line_quantity_non_negative"
        ),
        sa.CheckConstraint(
            "line_number >= 1", name="ck_invoice_line_number_positive"
        ),
        sa.CheckConstraint(
            f"kind <> 'INCLUDED'::{INVOICE_LINE_KIND_ENUM} "
            "OR (unit_price_micros = 0 AND amount_micros = 0)",
            name="ck_invoice_line_included_is_free",
        ),
        sa.CheckConstraint(
            f"kind <> 'OVERAGE'::{INVOICE_LINE_KIND_ENUM} OR limit_key IS NOT NULL",
            name="ck_invoice_line_overage_names_a_limit",
        ),
    )

    op.create_index(
        "ix_invoice_line_items_invoice_id", "invoice_line_items", ["invoice_id"]
    )
    op.create_index(
        "ix_invoice_line_items_price_book_entry_id",
        "invoice_line_items",
        ["price_book_entry_id"],
    )

    op.execute(INVOICE_IMMUTABILITY_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_invoices_finalized_immutable
        BEFORE UPDATE ON invoices
        FOR EACH ROW EXECUTE FUNCTION invoices_finalized_immutable();
        """
    )

    op.execute(LINE_IMMUTABILITY_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_invoice_line_items_finalized_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON invoice_line_items
        FOR EACH ROW EXECUTE FUNCTION invoice_line_items_finalized_immutable();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_invoice_line_items_finalized_immutable "
        "ON invoice_line_items"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS invoice_line_items_finalized_immutable()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_invoices_finalized_immutable ON invoices"
    )
    op.execute("DROP FUNCTION IF EXISTS invoices_finalized_immutable()")

    op.drop_table("invoice_line_items")
    op.execute("DROP INDEX IF EXISTS uq_invoices_subscription_period")
    op.execute("DROP INDEX IF EXISTS ix_invoices_open")
    op.drop_table("invoices")
    op.execute("DROP SEQUENCE IF EXISTS invoice_number_seq")

    postgresql.ENUM(name=INVOICE_LINE_KIND_ENUM).drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name=INVOICE_STATUS_ENUM).drop(op.get_bind(), checkfirst=True)
