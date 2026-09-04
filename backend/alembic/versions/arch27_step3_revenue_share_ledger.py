"""ARCH-27 Step 3 — rev-share agreements, payout periods and the ledger (EXPAND)

Revision ID: arch27_step3_revenue_share_ledger
Revises: arch27_step2_partner_tenancy
Create Date: 2026-09-04

WHAT MAKES INVARIANTS 3 AND 4 STRUCTURAL RATHER THAN REMEMBERED
===============================================================

`partner_rev_share_ledger.basis_class` splits every organization's revenue in
a period into exactly one of three classes, and a CHECK constraint per class
makes the wrong shape unrepresentable:

    ZERO_BYOK            supplier_cost_micros = 0
                         AND margin_micros = revenue_micros
    UNKNOWN_COST_BASIS   supplier_cost_micros IS NULL
                         AND margin_micros IS NULL
                         AND payout_micros = 0
    SUPPLIER_COST        supplier_cost_micros IS NOT NULL
                         AND margin_micros IS NOT NULL

Invariant 4 (ZERO_BYOK transparency) is then not a reporting convention that a
future query can forget: a ZERO_BYOK line cannot be merged into a
SUPPLIER_COST line, because the unique key is
`(payout_period_id, organization_id, basis_class)` and the constraints on the
two classes are mutually exclusive.

Invariant "unknown is never zero" — ARCH-18 gate check G2, ARCH-24's whole
thesis — is likewise structural here. `COALESCE(cost_basis_micros, 0)` in a
rev-share computation reads a tenant whose supplier cost we do not know as a
100% margin tenant, and pays a reseller on it. The UNKNOWN_COST_BASIS class
carries `payout_micros = 0` as a CHECK, so the anti-pattern cannot produce a
payable line even if somebody writes it.

Note there is deliberately NO `unknown_cost_basis_policy = 'ZERO'` option on
the agreement. The two permitted policies are EXCLUDE (record the revenue,
pay nothing on it, surface the exclusion) and FAIL (refuse to settle the
period at all). A configurable "treat unknown as free" would be a supported
route to the exact defect ARCH-18 exists to prevent.

WHY THE SEAL TRIGGER COMPARES BY SUBTRACTION, NOT BY COLUMN LIST
================================================================

`partner_payout_periods_seal_immutable()` builds `to_jsonb(OLD)` and
`to_jsonb(NEW)`, removes the columns that are legitimately mutable after
sealing, and refuses the UPDATE if what is left differs.

The alternative — enumerating the FROZEN columns and comparing each — is what
this schema does elsewhere and is the recorded defect: a column added to the
table in a later phase is not in the enumeration, so it is silently writable
on a sealed financial row. Subtracting the small, explicit allow-list means a
new column is frozen by default and somebody has to make a deliberate decision
to unfreeze it.

WHY CHECK CONSTRAINTS ARE CREATED SEPARATELY
============================================

`op.create_table` builds its Table in a temporary MetaData that does NOT carry
`app.db.base.NAMING_CONVENTION`, so a constraint declared inline as
`name="status_known"` lands in the database as `status_known` while the model
— whose MetaData does carry the convention — expects
`ck_partners_status_known`. Autogenerate then proposes dropping and recreating
every one of them, forever.

Every constraint below is therefore created after its table with the fully
qualified convention name spelled out. This is the ARCH-25/ARCH-26 precedent
and `verify_arch27.py` G17 fails if it is not followed.

WHY `supplier_cost_micros` AND `margin_micros` ARE NULLABLE ON THE PERIOD
========================================================================

Same asymmetry as `usage_rollups`: revenue is always known, because we refuse
to meter an event we cannot price. Supplier cost frequently is not. A period
in which every organization landed in UNKNOWN_COST_BASIS has a real revenue
figure and no margin figure at all, and the honest representation of that is
NULL — not 0, which reads as "we spent nothing" and is worth a payout.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch27_step3_revenue_share_ledger"
down_revision = "arch27_step2_partner_tenancy"
branch_labels = None
depends_on = None


DIGEST_LENGTH = 71
DIGEST_REGEX = "^sha256:[0-9a-f]{64}$"

AGREEMENT_BASIS_IN = "'GROSS_MARGIN', 'NET_REVENUE'"
AGREEMENT_STATUS_IN = "'ACTIVE', 'ENDED'"
UNKNOWN_POLICY_IN = "'EXCLUDE', 'FAIL'"
PERIOD_STATUS_IN = "'DRAFT', 'SEALED', 'PAID', 'VOID'"
BASIS_CLASS_IN = "'SUPPLIER_COST', 'ZERO_BYOK', 'UNKNOWN_COST_BASIS'"

#: Columns an UPDATE may still change once `partner_payout_periods.status`
#: has left DRAFT. Everything else — every figure the statement was computed
#: from — is frozen, INCLUDING any column a later phase adds, because the
#: trigger subtracts this list rather than enumerating the frozen half.
MUTABLE_AFTER_SEAL: tuple[str, ...] = (
    "status",
    "paid_at",
    "payment_reference",
    "settlement_notes",
    "updated_at",
)

_SEAL_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION partner_payout_periods_seal_immutable()
RETURNS TRIGGER AS $$
DECLARE
    frozen_old jsonb;
    frozen_new jsonb;
BEGIN
    IF OLD.status = 'DRAFT' THEN
        RETURN NEW;
    END IF;

    frozen_old := to_jsonb(OLD) - ARRAY[{mutable}];
    frozen_new := to_jsonb(NEW) - ARRAY[{mutable}];

    IF frozen_old IS DISTINCT FROM frozen_new THEN
        RAISE EXCEPTION
            'partner_payout_periods row % is sealed; only (%) may change '
            'after sealing. A sealed rev-share statement is what a partner '
            'was paid on.',
            OLD.id, '{mutable_human}'
        USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_partner_payout_periods_seal_immutable
    ON partner_payout_periods;

CREATE TRIGGER trg_partner_payout_periods_seal_immutable
    BEFORE UPDATE ON partner_payout_periods
    FOR EACH ROW
    EXECUTE FUNCTION partner_payout_periods_seal_immutable();
""".format(
    mutable=", ".join(f"'{c}'" for c in MUTABLE_AFTER_SEAL),
    mutable_human=", ".join(MUTABLE_AFTER_SEAL),
)

_LEDGER_APPEND_ONLY_SQL = """
CREATE OR REPLACE FUNCTION partner_rev_share_ledger_append_only()
RETURNS TRIGGER AS $$
DECLARE
    period_status text;
BEGIN
    SELECT status INTO period_status
      FROM partner_payout_periods
     WHERE id = COALESCE(NEW.payout_period_id, OLD.payout_period_id);

    IF period_status IS NOT NULL AND period_status <> 'DRAFT' THEN
        RAISE EXCEPTION
            'partner_rev_share_ledger is append-only once its payout period '
            'leaves DRAFT (period % is %). Restate by voiding the period and '
            'issuing a new one; never by editing a paid line.',
            COALESCE(NEW.payout_period_id, OLD.payout_period_id), period_status
        USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_partner_rev_share_ledger_append_only
    ON partner_rev_share_ledger;

CREATE TRIGGER trg_partner_rev_share_ledger_append_only
    BEFORE UPDATE OR DELETE ON partner_rev_share_ledger
    FOR EACH ROW
    EXECUTE FUNCTION partner_rev_share_ledger_append_only();
"""


def upgrade() -> None:
    # ---- partner_rev_share_agreements ------------------------------------
    op.create_table(
        "partner_rev_share_agreements",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "partner_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("partners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "basis",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'GROSS_MARGIN'"),
            comment="GROSS_MARGIN pays a share of (revenue - supplier cost). "
            "NET_REVENUE pays a share of revenue and ignores cost entirely; "
            "it exists because some contracts are written that way, and a "
            "contract this platform cannot express is a contract somebody "
            "computes in a spreadsheet instead.",
        ),
        sa.Column(
            "share_bps",
            sa.Integer(),
            nullable=False,
            comment="Basis points. Integer arithmetic end to end: a float "
            "share of a micros figure reintroduces the rounding drift the "
            "micros representation exists to eliminate.",
        ),
        sa.Column(
            "zero_byok_share_bps",
            sa.Integer(),
            nullable=True,
            comment="Optional distinct rate for ZERO_BYOK traffic, which "
            "carries 100% margin because the tenant pays the supplier "
            "directly. NULL means share_bps applies. Invariant 4: the class "
            "is always visible in the ledger whether or not the rate differs.",
        ),
        sa.Column(
            "currency", sa.String(length=3), nullable=False, server_default=sa.text("'USD'")
        ),
        sa.Column(
            "minimum_payout_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Below this the period seals with payout carried to "
            "`carried_forward_micros` rather than paid. Sealing still happens: "
            "an unsealed period is an unreproducible one.",
        ),
        sa.Column(
            "unknown_cost_basis_policy",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'EXCLUDE'"),
            comment="EXCLUDE records the revenue, pays nothing on it and "
            "surfaces the exclusion. FAIL refuses to settle. There is "
            "deliberately no third option that treats unknown supplier cost "
            "as zero.",
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'ACTIVE'")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    for _name, _condition in (
        ("basis_known", f"basis IN ({AGREEMENT_BASIS_IN})"),
        ("status_known", f"status IN ({AGREEMENT_STATUS_IN})"),
        ("unknown_policy_known", f"unknown_cost_basis_policy IN ({UNKNOWN_POLICY_IN})"),
        ("share_bps_ranged", "share_bps >= 0 AND share_bps <= 10000"),
        ("zero_byok_share_bps_ranged", "zero_byok_share_bps IS NULL OR " "(zero_byok_share_bps >= 0 AND zero_byok_share_bps <= 10000)"),
        ("minimum_payout_non_negative", "minimum_payout_micros >= 0"),
        ("currency_iso4217", "length(currency) = 3"),
        ("period_ordered", "effective_to IS NULL OR effective_to >= effective_from"),
        ("ended_has_end_date", "status <> 'ENDED' OR effective_to IS NOT NULL"),
    ):
        op.create_check_constraint(
            op.f(f"ck_partner_rev_share_agreements_{_name}"),
            "partner_rev_share_agreements",
            _condition,
        )
    # One live agreement per partner. Two overlapping ACTIVE agreements make
    # "which rate applies to this period" planner-dependent, which is the same
    # class of defect as two partners claiming one organization.
    op.create_index(
        "uq_partner_rev_share_agreements_active",
        "partner_rev_share_agreements",
        ["partner_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_partner_rev_share_agreements_partner_id",
        "partner_rev_share_agreements",
        ["partner_id"],
    )

    # ---- partner_payout_periods ------------------------------------------
    op.create_table(
        "partner_payout_periods",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "partner_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("partners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agreement_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("partner_rev_share_agreements.id", ondelete="RESTRICT"),
            nullable=False,
            comment="RESTRICT: the terms a statement was computed under must "
            "remain readable for as long as the statement does.",
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column(
            "period_end",
            sa.Date(),
            nullable=False,
            comment="Last day covered, INCLUSIVE — matching supplier_invoices.",
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'DRAFT'")
        ),
        sa.Column(
            "currency", sa.String(length=3), nullable=False, server_default=sa.text("'USD'")
        ),
        sa.Column(
            "gross_revenue_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Every micro of customer revenue attributable to the book "
            "in this period, INCLUDING revenue excluded from payout. A "
            "partner must be able to see what was set aside and why.",
        ),
        sa.Column(
            "supplier_cost_micros",
            sa.BigInteger(),
            nullable=True,
            comment="NULL when not one organization in the period carried a "
            "known basis. Never COALESCEd to 0.",
        ),
        sa.Column("margin_micros", sa.BigInteger(), nullable=True),
        sa.Column(
            "payout_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "carried_forward_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Payout withheld this period because it fell below the "
            "agreement minimum. Recorded rather than dropped.",
        ),
        # ---- Invariant 4 surfaced on the statement itself ----------------
        sa.Column(
            "zero_byok_revenue_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "zero_byok_margin_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "zero_byok_payout_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # ---- what was set aside, and how much ----------------------------
        sa.Column(
            "excluded_revenue_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "excluded_unknown_cost_basis_event_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "organization_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "source_rollup_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "content_digest",
            sa.String(length=DIGEST_LENGTH),
            nullable=False,
            server_default=sa.text("''"),
            comment="Empty while DRAFT. Written at seal over the canonical "
            "payload of the period plus every ledger line, so recomputing it "
            "a year later either reproduces the statement or proves it moved.",
        ),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_reference", sa.String(length=200), nullable=True),
        sa.Column("settlement_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # A sealed statement without a digest claims a settlement nobody can
        # verify — ARCH-15's ck_invoices_finalized_has_digest, carried forward.
    )

    for _name, _condition in (
        ("status_known", f"status IN ({PERIOD_STATUS_IN})"),
        ("period_ordered", "period_end >= period_start"),
        ("currency_iso4217", "length(currency) = 3"),
        ("revenue_non_negative", "gross_revenue_micros >= 0"),
        ("supplier_cost_non_negative", "supplier_cost_micros IS NULL OR supplier_cost_micros >= 0"),
        ("payout_non_negative", "payout_micros >= 0"),
        ("carried_forward_non_negative", "carried_forward_micros >= 0"),
        ("zero_byok_non_negative", "zero_byok_revenue_micros >= 0 AND zero_byok_margin_micros >= 0 " "AND zero_byok_payout_micros >= 0"),
        ("excluded_non_negative", "excluded_revenue_micros >= 0 " "AND excluded_unknown_cost_basis_event_count >= 0"),
        ("excluded_within_revenue", "excluded_revenue_micros <= gross_revenue_micros"),
        ("zero_byok_within_revenue", "zero_byok_revenue_micros <= gross_revenue_micros"),
        ("counts_non_negative", "organization_count >= 0 AND source_rollup_count >= 0"),
        ("sealed_has_digest", "status = 'DRAFT' OR (sealed_at IS NOT NULL AND content_digest <> '')"),
        ("digest_shape", f"content_digest = '' OR content_digest ~ '{DIGEST_REGEX}'"),
        ("paid_has_timestamp", "status <> 'PAID' OR paid_at IS NOT NULL"),
        ("draft_is_unsettled", "status <> 'DRAFT' OR (sealed_at IS NULL AND paid_at IS NULL)"),
    ):
        op.create_check_constraint(
            op.f(f"ck_partner_payout_periods_{_name}"),
            "partner_payout_periods",
            _condition,
        )
    op.create_index(
        "uq_partner_payout_periods_partner_period",
        "partner_payout_periods",
        ["partner_id", "period_start", "period_end"],
        unique=True,
    )
    op.create_index(
        "ix_partner_payout_periods_partner_status",
        "partner_payout_periods",
        ["partner_id", "status"],
    )
    op.create_index(
        "ix_partner_payout_periods_unsettled",
        "partner_payout_periods",
        ["partner_id", "period_start"],
        postgresql_where=sa.text("status = 'SEALED'"),
    )

    # ---- partner_rev_share_ledger ----------------------------------------
    op.create_table(
        "partner_rev_share_ledger",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "payout_period_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("partner_payout_periods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "partner_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("partners.id", ondelete="CASCADE"),
            nullable=False,
            comment="Denormalised from the period so that book-scoped ledger "
            "reads are one index lookup and never a join a future query can "
            "forget to write.",
        ),
        sa.Column(
            "organization_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
            comment="RESTRICT: deleting a tenant must not silently erase the "
            "line a reseller was paid on. ARCH-20 erasure replaces subject "
            "identifiers; it does not delete organizations.",
        ),
        sa.Column(
            "basis_class",
            sa.String(length=24),
            nullable=False,
            comment="SUPPLIER_COST | ZERO_BYOK | UNKNOWN_COST_BASIS. The unit "
            "of invariant 4: a ZERO_BYOK line can never be folded into a "
            "SUPPLIER_COST line because their CHECK constraints are mutually "
            "exclusive and the unique key separates them.",
        ),
        sa.Column("revenue_micros", sa.BigInteger(), nullable=False),
        sa.Column("supplier_cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("margin_micros", sa.BigInteger(), nullable=True),
        sa.Column("share_bps", sa.Integer(), nullable=False),
        sa.Column(
            "payout_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "unknown_cost_basis_event_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "source_rollup_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="Sorted list of the sealed usage_rollups this line was "
            "computed from. Invariant 3: reproducibility means naming the "
            "inputs, not merely storing the output.",
        ),
        sa.Column(
            "cost_basis_source_mix",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Event counts by ARCH-18 cost basis source, summed across "
            "the source rollups. This is what proves a ZERO_BYOK "
            "classification rather than asserting it.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # Invariant 4, and the "unknown is never zero" invariant, as CHECKs.
    )

    for _name, _condition in (
        ("basis_class_known", f"basis_class IN ({BASIS_CLASS_IN})"),
        ("revenue_non_negative", "revenue_micros >= 0"),
        ("payout_non_negative", "payout_micros >= 0"),
        ("share_bps_ranged", "share_bps >= 0 AND share_bps <= 10000"),
        ("counts_non_negative", "event_count >= 0 AND unknown_cost_basis_event_count >= 0"),
        ("unknown_within_events", "unknown_cost_basis_event_count <= event_count"),
        ("zero_byok_is_full_margin", "basis_class <> 'ZERO_BYOK' OR (" " supplier_cost_micros = 0 AND margin_micros = revenue_micros" " AND unknown_cost_basis_event_count = 0)"),
        ("unknown_pays_nothing", "basis_class <> 'UNKNOWN_COST_BASIS' OR (" " supplier_cost_micros IS NULL AND margin_micros IS NULL" " AND payout_micros = 0)"),
        ("supplier_cost_is_complete", "basis_class <> 'SUPPLIER_COST' OR (" " supplier_cost_micros IS NOT NULL AND margin_micros IS NOT NULL" " AND unknown_cost_basis_event_count = 0)"),
        ("margin_is_revenue_less_cost", "margin_micros IS NULL OR supplier_cost_micros IS NULL" " OR margin_micros = revenue_micros - supplier_cost_micros"),
        ("source_rollup_ids_is_array", "jsonb_typeof(source_rollup_ids) = 'array'"),
    ):
        op.create_check_constraint(
            op.f(f"ck_partner_rev_share_ledger_{_name}"),
            "partner_rev_share_ledger",
            _condition,
        )
    op.create_index(
        "uq_partner_rev_share_ledger_line",
        "partner_rev_share_ledger",
        ["payout_period_id", "organization_id", "basis_class"],
        unique=True,
    )
    op.create_index(
        "ix_partner_rev_share_ledger_partner_org",
        "partner_rev_share_ledger",
        ["partner_id", "organization_id"],
    )
    op.create_index(
        "ix_partner_rev_share_ledger_zero_byok",
        "partner_rev_share_ledger",
        ["payout_period_id"],
        postgresql_where=sa.text("basis_class = 'ZERO_BYOK'"),
    )

    op.execute(_SEAL_TRIGGER_SQL)
    op.execute(_LEDGER_APPEND_ONLY_SQL)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_partner_rev_share_ledger_append_only "
        "ON partner_rev_share_ledger"
    )
    op.execute("DROP FUNCTION IF EXISTS partner_rev_share_ledger_append_only()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_partner_payout_periods_seal_immutable "
        "ON partner_payout_periods"
    )
    op.execute("DROP FUNCTION IF EXISTS partner_payout_periods_seal_immutable()")
    op.drop_table("partner_rev_share_ledger")
    op.drop_table("partner_payout_periods")
    op.drop_table("partner_rev_share_agreements")