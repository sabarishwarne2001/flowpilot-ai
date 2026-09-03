"""ARCH-24 Step 1 — rollup cost basis + reconciliation method annotation (EXPAND)

Revision ID: arch24_step1_rollup_cost_basis
Revises: arch23_step1_azure_credential_shape
Create Date: 2026-09-02

Three columns on `usage_rollups` and one discriminator on each of the two
reconciliation tables. Nothing is recomputed and nothing is back-written.

WHY THE IMMUTABILITY TRIGGER IS NOT TOUCHED HERE (decision D1)
==============================================================
The ARCH-24 brief asked for `usage_rollups_seal_immutable()` to be "updated so
the new columns are covered". The audit found that instruction rests on a false
premise. That function is a *blanket* deny:

    IF OLD.sealed_at IS NULL THEN RETURN NEW; END IF;
    RAISE EXCEPTION '... is sealed at %; a late event belongs in the open
                     bucket, not in an invoiced one' USING ERRCODE = '42501';

Every UPDATE against a sealed row raises, whatever column moved. The new columns
are therefore covered the instant they exist. The column-enumerating function in
this schema is `rollup_windows_seal_immutable()`, on a different table.

Rewriting the rollup function to enumerate columns would convert a deny-all into
an allowlist — a strict downgrade, and the ARCH-18 defect class running
backwards. So this migration leaves it alone and instead *asserts* the shape it
depends on, below. If a future migration enumerates columns there, this
assertion is the thing that will notice.

The consequence, stated plainly because it should be disclosed rather than
discovered: sealed rollups can never acquire a cost basis. `cost_basis_micros`
is NULL for all history and populates forward-only.

WHY THE BACKFILL IS A server_default AND NOT AN UPDATE
======================================================
`reconciliation_runs_immutable()` refuses every UPDATE on a row whose status has
left 'RUNNING', so a backfill UPDATE against reconciliation history would raise
42501 for every historical row. `ALTER TABLE ... ADD COLUMN ... DEFAULT` fills
existing rows as DDL, which does not fire row triggers. The historical value is
therefore written by the ADD COLUMN itself, and the going-forward default is set
separately afterwards where the two differ.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch24_step1_rollup_cost_basis"
down_revision = "arch23_step1_azure_credential_shape"
branch_labels = None
depends_on = None


# The three discriminator values. Kept as literals here rather than imported
# from app.models so the migration remains runnable against a checkout whose
# application code has moved on.
METHOD_ARCH18_SUPPLIER_COST = "ARCH18_SUPPLIER_COST"
METHOD_ARCH18_PRE_CONSOLIDATION = "ARCH18_PRE_CONSOLIDATION"
METHOD_ARCH14_SELL_SIDE = "ARCH14_SELL_SIDE"

_METHOD_IN = ", ".join(
    f"'{value}'"
    for value in (
        METHOD_ARCH18_SUPPLIER_COST,
        METHOD_ARCH18_PRE_CONSOLIDATION,
        METHOD_ARCH14_SELL_SIDE,
    )
)


ASSERT_BLANKET_DENY = """
DO $$
DECLARE
    body text;
BEGIN
    SELECT pg_get_functiondef(p.oid) INTO body
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE p.proname = 'usage_rollups_seal_immutable'
       AND n.nspname = current_schema();

    IF body IS NULL THEN
        RAISE EXCEPTION
            'usage_rollups_seal_immutable() is missing. ARCH-24 adds financial '
            'columns to usage_rollups on the understanding that sealed rows '
            'cannot be updated at all. Without that function the guarantee is '
            'gone and this migration must not proceed.'
            USING ERRCODE = '42501';
    END IF;

    IF body ILIKE '%IS DISTINCT FROM%' THEN
        RAISE EXCEPTION
            'usage_rollups_seal_immutable() now enumerates columns. ARCH-24 '
            'decision D1 depends on it being a blanket deny: every UPDATE on a '
            'sealed row raises, whatever column moved. An allowlist would '
            'leave cost_basis_micros writable on invoiced periods. Restore the '
            'blanket form before adding financial columns.'
            USING ERRCODE = '42501';
    END IF;
END
$$;
"""


def upgrade() -> None:
    # ---- 0. the guarantee this migration is standing on ------------------
    op.execute(ASSERT_BLANKET_DENY)

    # ---- 1. usage_rollups cost basis -------------------------------------
    #
    # Nullable, deliberately. A bucket in which no event carried a cost basis
    # rolls up to NULL, never to 0. `COALESCE(cost_basis_micros, 0)` silently
    # produces a 100% gross margin and someone will price an enterprise
    # contract on it.
    op.add_column(
        "usage_rollups",
        sa.Column("cost_basis_micros", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "usage_rollups",
        sa.Column(
            "unknown_cost_basis_event_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "usage_rollups",
        sa.Column(
            "cost_basis_source_mix",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    op.create_check_constraint(
        "cost_basis_non_negative",
        "usage_rollups",
        "cost_basis_micros IS NULL OR cost_basis_micros >= 0",
    )
    op.create_check_constraint(
        "unknown_cost_basis_count_non_negative",
        "usage_rollups",
        "unknown_cost_basis_event_count >= 0",
    )
    # An unknown-basis count cannot exceed the events actually in the bucket.
    # Without this a writer bug can report a bucket as more untrustworthy than
    # it is possible for it to be, and the margins hub would show a share above
    # 100%.
    op.create_check_constraint(
        "unknown_cost_basis_within_events",
        "usage_rollups",
        "unknown_cost_basis_event_count <= event_count",
    )

    # Finding buckets whose cost basis is incomplete is the query the margins
    # hub runs; it is a small minority of rows, so it is worth a partial index.
    op.create_index(
        "ix_usage_rollups_unknown_cost_basis",
        "usage_rollups",
        ["organization_id", "granularity", "bucket_start"],
        postgresql_where=sa.text("unknown_cost_basis_event_count > 0"),
    )

    op.execute(
        "COMMENT ON COLUMN usage_rollups.cost_basis_micros IS "
        "'Sum of supplier cost over events in this bucket that carried a cost "
        "basis. NULL when no event in the bucket carried one. Never 0 by "
        "coalesce. Forward-only: sealed buckets predate ARCH-24 and stay NULL.'"
    )
    op.execute(
        "COMMENT ON COLUMN usage_rollups.unknown_cost_basis_event_count IS "
        "'Events folded into this bucket with no cost basis. A non-zero value "
        "means cost_basis_micros is a partial sum and the bucket is not "
        "trustworthy for margin.'"
    )
    op.execute(
        "COMMENT ON COLUMN usage_rollups.cost_basis_source_mix IS "
        "'{source: event_count} over COST_BASIS_SOURCE_VALUES for the events "
        "that did carry a basis. Lets a reader see an ESTIMATED-heavy bucket.'"
    )

    # ---- 2. supplier_reconciliations: the surviving cost authority --------
    #
    # ADD COLUMN ... DEFAULT stamps every existing row as pre-consolidation in
    # one DDL pass. Then the going-forward default becomes the post-ARCH-24
    # value. Historical rows are annotated; not one number is recomputed.
    op.add_column(
        "supplier_reconciliations",
        sa.Column(
            "cost_basis_method",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text(f"'{METHOD_ARCH18_PRE_CONSOLIDATION}'"),
        ),
    )
    op.alter_column(
        "supplier_reconciliations",
        "cost_basis_method",
        server_default=sa.text(f"'{METHOD_ARCH18_SUPPLIER_COST}'"),
    )
    op.create_check_constraint(
        "cost_basis_method_known",
        "supplier_reconciliations",
        f"cost_basis_method IN ({_METHOD_IN})",
    )

    # ---- 3. reconciliation_runs: the retired cost dimension ---------------
    #
    # Every ARCH-14 run, past and future, is sell-side denominated. Its drift
    # figures are gross-margin-inflated against supplier cost and must never be
    # read as COGS variance. One value, no default change needed.
    op.add_column(
        "reconciliation_runs",
        sa.Column(
            "cost_basis_method",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text(f"'{METHOD_ARCH14_SELL_SIDE}'"),
        ),
    )
    op.create_check_constraint(
        "cost_basis_method_known",
        "reconciliation_runs",
        f"cost_basis_method IN ({_METHOD_IN})",
    )

    op.execute(
        "COMMENT ON COLUMN reconciliation_runs.cost_basis_method IS "
        "'Always ARCH14_SELL_SIDE. This engine reconciles volume and price-book "
        "drift; its micros are customer price, not supplier cost. ARCH-18 "
        "supplier_reconciliations is the sole cost-variance authority.'"
    )
    op.execute(
        "COMMENT ON COLUMN supplier_reconciliations.cost_basis_method IS "
        "'ARCH18_PRE_CONSOLIDATION for rows written before ARCH-24, "
        "ARCH18_SUPPLIER_COST after. Annotation only — no historical variance "
        "was recomputed.'"
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_reconciliation_runs_cost_basis_method_known",
        "reconciliation_runs",
        type_="check",
    )
    op.drop_column("reconciliation_runs", "cost_basis_method")

    op.drop_constraint(
        "ck_supplier_reconciliations_cost_basis_method_known",
        "supplier_reconciliations",
        type_="check",
    )
    op.drop_column("supplier_reconciliations", "cost_basis_method")

    op.drop_index("ix_usage_rollups_unknown_cost_basis", table_name="usage_rollups")
    op.drop_constraint(
        "ck_usage_rollups_unknown_cost_basis_within_events",
        "usage_rollups",
        type_="check",
    )
    op.drop_constraint(
        "ck_usage_rollups_unknown_cost_basis_count_non_negative",
        "usage_rollups",
        type_="check",
    )
    op.drop_constraint(
        "ck_usage_rollups_cost_basis_non_negative", "usage_rollups", type_="check"
    )
    op.drop_column("usage_rollups", "cost_basis_source_mix")
    op.drop_column("usage_rollups", "unknown_cost_basis_event_count")
    op.drop_column("usage_rollups", "cost_basis_micros")