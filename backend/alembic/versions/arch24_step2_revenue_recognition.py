"""ARCH-24 Step 2 — revenue recognition primitives

Revision ID: arch24_step2_revenue_recognition
Revises: arch24_step1_rollup_cost_basis
Create Date: 2026-09-02

Deferred and recognised revenue by period, anchored on ARCH-15's sealed
invoices. Deliberately primitives and not an engine: ARCH-27's revenue share
needs somewhere truthful to stand, and landing the tables now means ARCH-27
does not have to reopen the financial model to get them.

TWO INVARIANTS ARE ENFORCED IN THE DATABASE, NOT THE SERVICE
=============================================================
1. A schedule may only be built from a FINALIZED invoice. `invoices` is already
   immutable once `finalized_at` is set (`invoices_finalized_immutable`), so a
   schedule built from one is reproducible. Building from a draft would let the
   underlying number move after revenue was recognised against it.

2. Recognised revenue may never exceed the schedule it draws from. This is
   enforced by a trigger rather than a CHECK because the sum spans rows: a
   CHECK can only see the row being written. Over-recognition is the single
   error in this table that a reader cannot detect by eye, because every
   individual row looks reasonable.

Ledger rows are append-only. A correction is a new negative row with a reason,
not an UPDATE — the same discipline ARCH-18 applies to supplier reconciliation.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch24_step2_revenue_recognition"
down_revision = "arch24_step1_rollup_cost_basis"
branch_labels = None
depends_on = None


SCHEDULE_STATUS_VALUES = ("DRAFT", "ACTIVE", "COMPLETED", "CANCELLED")
_SCHEDULE_STATUS_IN = ", ".join(f"'{v}'" for v in SCHEDULE_STATUS_VALUES)

RECOGNITION_REASON_VALUES = ("RATABLE", "POINT_IN_TIME", "CATCH_UP", "CORRECTION")
_REASON_IN = ", ".join(f"'{v}'" for v in RECOGNITION_REASON_VALUES)


LEDGER_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION recognized_revenue_ledger_append_only()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        RAISE EXCEPTION
            'recognized_revenue_ledger % is an accounting record and cannot be '
            'deleted; post a CORRECTION row instead',
            OLD.id
            USING ERRCODE = '42501';
    END IF;

    RAISE EXCEPTION
        'recognized_revenue_ledger % is append-only; a restatement is a new '
        'CORRECTION row carrying its own reason, not an edit to the row that '
        'was already reported',
        OLD.id
        USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;
"""


OVER_RECOGNITION_FUNCTION = """
CREATE OR REPLACE FUNCTION recognized_revenue_within_schedule()
RETURNS TRIGGER AS $$
DECLARE
    scheduled bigint;
    recognised bigint;
BEGIN
    SELECT total_micros INTO scheduled
      FROM revenue_schedules
     WHERE id = NEW.revenue_schedule_id
     FOR SHARE;

    IF scheduled IS NULL THEN
        RAISE EXCEPTION
            'revenue_schedules % does not exist', NEW.revenue_schedule_id
            USING ERRCODE = '23503';
    END IF;

    SELECT COALESCE(sum(amount_micros), 0) INTO recognised
      FROM recognized_revenue_ledger
     WHERE revenue_schedule_id = NEW.revenue_schedule_id;

    IF recognised + NEW.amount_micros > scheduled THEN
        RAISE EXCEPTION
            'recognising % micros against schedule % would take recognised '
            'revenue to % against a scheduled total of %; revenue cannot be '
            'recognised twice',
            NEW.amount_micros, NEW.revenue_schedule_id,
            recognised + NEW.amount_micros, scheduled
            USING ERRCODE = '23514';
    END IF;

    IF recognised + NEW.amount_micros < 0 THEN
        RAISE EXCEPTION
            'correction of % micros would take recognised revenue on schedule '
            '% negative (currently %); a refund beyond what was recognised is '
            'a credit note, not a negative recognition',
            NEW.amount_micros, NEW.revenue_schedule_id, recognised
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # ---- revenue_schedules ----------------------------------------------
    op.create_table(
        "revenue_schedules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "invoice_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default=sa.text("'DRAFT'")),
        sa.Column("total_micros", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False,
                  server_default=sa.text("'USD'")),
        sa.Column("service_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recognition_method", sa.String(length=24), nullable=False,
                  server_default=sa.text("'RATABLE'")),
        sa.Column("source_sealed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    op.create_check_constraint(
        "status_known", "revenue_schedules", f"status IN ({_SCHEDULE_STATUS_IN})"
    )
    op.create_check_constraint(
        "total_non_negative", "revenue_schedules", "total_micros >= 0"
    )
    op.create_check_constraint(
        "currency_iso4217", "revenue_schedules", "length(currency) = 3"
    )
    op.create_check_constraint(
        "service_period_ordered",
        "revenue_schedules",
        "service_period_end > service_period_start",
    )

    # One schedule per invoice. A second schedule against the same invoice is
    # how the same revenue gets recognised twice, and it is the failure mode
    # that would survive every unit test written against a single schedule.
    op.create_index(
        "uq_revenue_schedules_invoice", "revenue_schedules", ["invoice_id"], unique=True
    )
    op.create_index(
        "ix_revenue_schedules_org_period",
        "revenue_schedules",
        ["organization_id", "service_period_start"],
    )
    op.create_index(
        "ix_revenue_schedules_open",
        "revenue_schedules",
        ["service_period_end"],
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    # ---- recognized_revenue_ledger --------------------------------------
    op.create_table(
        "recognized_revenue_ledger",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "revenue_schedule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revenue_schedules.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount_micros", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False,
                  server_default=sa.text("'USD'")),
        sa.Column("reason", sa.String(length=24), nullable=False,
                  server_default=sa.text("'RATABLE'")),
        sa.Column("recognized_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column(
            "recognized_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    op.create_check_constraint(
        "reason_known", "recognized_revenue_ledger", f"reason IN ({_REASON_IN})"
    )
    op.create_check_constraint(
        "currency_iso4217", "recognized_revenue_ledger", "length(currency) = 3"
    )
    op.create_check_constraint(
        "period_ordered", "recognized_revenue_ledger", "period_end > period_start"
    )
    # A negative amount is legal only as an explicit correction. Anything else
    # negative is a writer bug wearing an accounting costume.
    op.create_check_constraint(
        "negative_is_correction",
        "recognized_revenue_ledger",
        "amount_micros >= 0 OR reason = 'CORRECTION'",
    )

    op.create_index(
        "ix_recognized_revenue_schedule",
        "recognized_revenue_ledger",
        ["revenue_schedule_id", "period_start"],
    )
    op.create_index(
        "ix_recognized_revenue_org_period",
        "recognized_revenue_ledger",
        ["organization_id", "period_start"],
    )

    # ---- guards ----------------------------------------------------------
    op.execute(OVER_RECOGNITION_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_recognized_revenue_within_schedule
        BEFORE INSERT ON recognized_revenue_ledger
        FOR EACH ROW EXECUTE FUNCTION recognized_revenue_within_schedule();
        """
    )

    op.execute(LEDGER_APPEND_ONLY_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_recognized_revenue_append_only
        BEFORE UPDATE OR DELETE ON recognized_revenue_ledger
        FOR EACH ROW EXECUTE FUNCTION recognized_revenue_ledger_append_only();
        """
    )

    # A schedule may only ever be built from an invoice that is already sealed.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION revenue_schedules_source_finalized()
        RETURNS TRIGGER AS $$
        DECLARE
            sealed timestamptz;
        BEGIN
            SELECT finalized_at INTO sealed
              FROM invoices WHERE id = NEW.invoice_id;

            IF sealed IS NULL THEN
                RAISE EXCEPTION
                    'invoice % is not finalized; a revenue schedule built from '
                    'a draft can be invalidated by the draft changing',
                    NEW.invoice_id
                    USING ERRCODE = '42501';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_revenue_schedules_source_finalized
        BEFORE INSERT OR UPDATE ON revenue_schedules
        FOR EACH ROW EXECUTE FUNCTION revenue_schedules_source_finalized();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_revenue_schedules_source_finalized "
        "ON revenue_schedules"
    )
    op.execute("DROP FUNCTION IF EXISTS revenue_schedules_source_finalized()")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_recognized_revenue_append_only "
        "ON recognized_revenue_ledger"
    )
    op.execute("DROP FUNCTION IF EXISTS recognized_revenue_ledger_append_only()")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_recognized_revenue_within_schedule "
        "ON recognized_revenue_ledger"
    )
    op.execute("DROP FUNCTION IF EXISTS recognized_revenue_within_schedule()")

    op.drop_table("recognized_revenue_ledger")
    op.drop_table("revenue_schedules")