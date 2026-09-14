"""ARCH-31 Step 1 — procurement cases, case lines and tolerance policies (EXPAND)

Revision ID: arch31_step1_procurement_matching
Revises: arch31_step1_procurement_vocabulary
Create Date: 2026-09-14

EXPAND-SHAPED
=============

Three new tables and one trigger function. Nothing existing is altered, so
this is safe to run before the code that reads it ships and safe to leave in
place if the phase is reverted.

WHY `procurement_*` AND NOT `reconciliation_*`
==============================================

`app/models/reconciliation.py` and `app/services/reconciliation/` are ARCH-18
supplier COGS: they match the invoices Anthropic and Groq send US against our
own metered usage. ARCH-31 matches a TENANT's purchase order against their
goods receipt against their supplier's invoice. Same English word, unrelated
problems, and the first person to grep `reconcil` six months from now would
find both and trust the wrong one.

THE THREE CONSTRAINTS THAT CARRY THE DESIGN
===========================================

1. `ck_procurement_cases_two_sided`. At least two of the three document
   references must be present. A "case" with one document is not a match, it
   is a document — and permitting it would let the queue fill with
   single-invoice rows that can never be anything but NOT_ORDERED, drowning
   the cases a human can actually act on.

   Two is the floor rather than three because two-way matching is a real and
   common mode: many tenants have no goods receipt for services, and an
   invoice against a PO with no receipt is a legitimate case whose every line
   is correctly NOT_RECEIVED.

2. `uq_procurement_cases_live_invoice`. One LIVE case per (workspace,
   invoice). Partial, excluding SUPERSEDED, because superseding is how a
   re-score under a new policy retires the old case — the old row stays for
   the audit trail and must not block the new one.

   Partial also on `invoice_work_item_id IS NOT NULL`: a PO-vs-receipt case
   with no invoice yet is legal (constraint 1 allows it) and several of them
   in one workspace must not collide on NULL.

3. `trg_procurement_tolerance_policies_immutable`. A PUBLISHED policy cannot
   be updated or deleted. This is enforced at the database rather than in the
   service because the thing it protects is the meaning of every case scored
   under it: `procurement_cases.policy_version` is a text stamp, and if the
   row behind that stamp can change, the stamp stops identifying anything and
   every historical case silently re-interprets itself.

   The trigger permits DRAFT -> PUBLISHED (that is an update on a DRAFT row,
   where OLD.status is still DRAFT) and refuses everything after.

WHY THERE IS NO `superseded_at` ON THE POLICY
=============================================

Recording that a published policy had been superseded would itself be an
update to a published row, which the trigger forbids. The current policy for a
workspace is instead derived: the PUBLISHED row with the highest `version`.
That makes "which policy governs now" a query rather than a column somebody
has to remember to maintain, and it is one fewer write the trigger has to
carve an exception for.

MONEY AND QUANTITY REPRESENTATION
=================================

Amounts are BigInteger micros, signed, matching the codebase-wide convention
established in ARCH-14 and reused by `app/core/normalize.py`. Quantities are
Numeric(20, 6) rather than micros: a quantity is not money, it is routinely
fractional (2.5 kg, 0.75 hours), and forcing it through an integer
representation would introduce a rounding decision at the exact point where a
QUANTITY_VARIANCE is decided.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch31_step1_procurement_matching"
down_revision = "arch31_step1_procurement_vocabulary"
branch_labels = None
depends_on = None


CASE_STATUSES: tuple[str, ...] = (
    "OPEN",
    "MATCHED",
    "NEEDS_REVIEW",
    "APPROVED",
    "DISPUTED",
    "SUPERSEDED",
)

LINE_OUTCOMES: tuple[str, ...] = (
    "MATCHED",
    "PRICE_VARIANCE",
    "QUANTITY_VARIANCE",
    "NOT_RECEIVED",
    "NOT_ORDERED",
    "NOT_INVOICED",
)

POLICY_STATUSES: tuple[str, ...] = ("DRAFT", "PUBLISHED")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # procurement_tolerance_policies
    #
    # Created first: procurement_cases carries no FK to it (the case stamps
    # a text `policy_version` instead, so a case survives the policy row
    # being unreachable), but the ordering keeps the read of this file
    # matching the order a human would design them in.
    # ------------------------------------------------------------------
    op.create_table(
        "procurement_tolerance_policies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            comment=(
                "Monotonic per workspace. Publishing never edits: it creates "
                "the next version. `procurement_cases.policy_version` stamps "
                "this, so it must never be reused."
            ),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'DRAFT'"),
        ),
        sa.Column(
            "price_tolerance_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment=(
                "Absolute price slack per line, in micros. Applied together "
                "with price_tolerance_bps; a line is within tolerance if it "
                "satisfies EITHER. An absolute-only policy punishes small "
                "lines, a relative-only policy waves through large ones."
            ),
        ),
        sa.Column(
            "price_tolerance_bps",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Relative price slack per line, in basis points of the PO price.",
        ),
        sa.Column(
            "quantity_tolerance",
            sa.Numeric(precision=20, scale=6),
            nullable=False,
            server_default=sa.text("0"),
            comment=(
                "Absolute quantity slack per line, in the line's own unit. "
                "Not a ratio: over-delivery allowances are written in units "
                "on every purchasing contract this will meet."
            ),
        ),
        sa.Column(
            "max_pair_cost",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("600000"),
            comment=(
                "Post-assignment rejection threshold, in the matcher's "
                "integer cost units (0..1000000). A minimum-cost assignment "
                "pairs EVERY row it can, including rows that have nothing to "
                "do with each other; without this ceiling the matcher "
                "invents a relationship rather than reporting NOT_ORDERED."
            ),
        ),
        sa.Column(
            "candidate_window_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("90"),
            comment=(
                "Half-width of the vendor date window used when no PO "
                "reference is printed on the invoice."
            ),
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "published_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
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
    )

    op.create_check_constraint(
        "ck_procurement_tolerance_policies_status",
        "procurement_tolerance_policies",
        f"status IN ({_quoted(POLICY_STATUSES)})",
    )
    op.create_check_constraint(
        "ck_procurement_tolerance_policies_version_positive",
        "procurement_tolerance_policies",
        "version >= 1",
    )
    op.create_check_constraint(
        "ck_procurement_tolerance_policies_non_negative",
        "procurement_tolerance_policies",
        "price_tolerance_micros >= 0 AND price_tolerance_bps >= 0 "
        "AND quantity_tolerance >= 0 AND candidate_window_days >= 0",
    )
    # The cost scale is 0..1_000_000 by construction in matcher.py. A
    # threshold at or above the ceiling rejects nothing, which is the same as
    # having no threshold — the defect this whole column exists to prevent.
    op.create_check_constraint(
        "ck_procurement_tolerance_policies_pair_cost_range",
        "procurement_tolerance_policies",
        "max_pair_cost > 0 AND max_pair_cost < 1000000",
    )
    # A PUBLISHED row must carry the moment it was published: `published_at`
    # is what orders two versions published in the same transaction, and a
    # NULL there makes "which policy governed on the 3rd?" unanswerable.
    op.create_check_constraint(
        "ck_procurement_tolerance_policies_published_has_timestamp",
        "procurement_tolerance_policies",
        "status <> 'PUBLISHED' OR published_at IS NOT NULL",
    )

    op.create_unique_constraint(
        "uq_procurement_tolerance_policies_version",
        "procurement_tolerance_policies",
        ["workspace_id", "version"],
    )
    # At most one draft per workspace. Two concurrent drafts would give the
    # editor two "current" answers and the publish button an ambiguous
    # target.
    op.create_index(
        "uq_procurement_tolerance_policies_one_draft",
        "procurement_tolerance_policies",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("status = 'DRAFT'"),
    )
    op.create_index(
        "ix_procurement_tolerance_policies_current",
        "procurement_tolerance_policies",
        ["workspace_id", "version"],
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )
    op.create_index(
        "ix_procurement_tolerance_policies_organization",
        "procurement_tolerance_policies",
        ["organization_id"],
    )

    # ------------------------------------------------------------------
    # procurement_cases
    # ------------------------------------------------------------------
    op.create_table(
        "procurement_cases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "po_work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "receipt_work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "invoice_work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'OPEN'"),
        ),
        sa.Column(
            "input_digest",
            sa.String(length=64),
            nullable=False,
            comment=(
                "SHA-256 over the canonical normalised inputs AND the policy "
                "version AND the matcher engine/similarity-backend "
                "identifiers. A digest that omits any of those lets a change "
                "to that input silently reuse a stale case."
            ),
        ),
        sa.Column(
            "policy_version",
            sa.String(length=64),
            nullable=False,
            comment=(
                "Text stamp, not an FK: '<policy_id>:<version>' or "
                "'default:1'. A case must remain interpretable after the "
                "workspace is re-pointed at a different policy lineage."
            ),
        ),
        sa.Column(
            "vendor_key",
            sa.String(length=255),
            nullable=True,
            comment="Denormalised from document_roles so the queue can filter without a join.",
        ),
        sa.Column(
            "header_findings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment=(
                "Self-consistency and header-level findings, produced BEFORE "
                "any cross-document comparison. An invoice whose own lines do "
                "not sum to its own total is flagged here first: comparing a "
                "document that disagrees with itself against a second "
                "document produces confident nonsense."
            ),
        ),
        sa.Column(
            "line_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "exception_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Lines whose outcome is not MATCHED. The queue's red-line count.",
        ),
        sa.Column(
            "variance_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Signed. Positive means the invoice asks for more than the PO agreed.",
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "resolution_reason",
            sa.Text(),
            nullable=True,
            comment=(
                "The override reason on APPROVED-with-red-lines, or the "
                "dispute reason on DISPUTED. Required by the service in both "
                "cases; nullable here because OPEN and MATCHED rows have no "
                "reason to carry one."
            ),
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
    )

    op.create_check_constraint(
        "ck_procurement_cases_status",
        "procurement_cases",
        f"status IN ({_quoted(CASE_STATUSES)})",
    )
    op.create_check_constraint(
        "ck_procurement_cases_two_sided",
        "procurement_cases",
        "(CASE WHEN po_work_item_id IS NOT NULL THEN 1 ELSE 0 END + "
        "CASE WHEN receipt_work_item_id IS NOT NULL THEN 1 ELSE 0 END + "
        "CASE WHEN invoice_work_item_id IS NOT NULL THEN 1 ELSE 0 END) >= 2",
    )
    op.create_check_constraint(
        "ck_procurement_cases_counts_non_negative",
        "procurement_cases",
        "line_count >= 0 AND exception_count >= 0 "
        "AND exception_count <= line_count",
    )
    # A digest is a SHA-256 hex string or it is not a digest. An empty string
    # would satisfy NOT NULL and collide with every other empty string,
    # merging unrelated cases on the idempotency path.
    op.create_check_constraint(
        "ck_procurement_cases_digest_shape",
        "procurement_cases",
        "input_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_procurement_cases_resolution_recorded",
        "procurement_cases",
        "status NOT IN ('APPROVED', 'DISPUTED') OR resolved_at IS NOT NULL",
    )

    op.create_index(
        "uq_procurement_cases_live_invoice",
        "procurement_cases",
        ["workspace_id", "invoice_work_item_id"],
        unique=True,
        postgresql_where=sa.text(
            "invoice_work_item_id IS NOT NULL AND status <> 'SUPERSEDED'"
        ),
    )
    # The queue: "open cases in this workspace, newest first", with the
    # variance filter applied on exception_count.
    op.create_index(
        "ix_procurement_cases_queue",
        "procurement_cases",
        ["workspace_id", "status", sa.text("created_at DESC")],
    )
    # Idempotent re-score: "have we already scored exactly this input?"
    op.create_index(
        "ix_procurement_cases_digest",
        "procurement_cases",
        ["workspace_id", "input_digest"],
    )
    op.create_index(
        "ix_procurement_cases_organization",
        "procurement_cases",
        ["organization_id"],
    )
    op.create_index(
        "ix_procurement_cases_vendor",
        "procurement_cases",
        ["workspace_id", "vendor_key"],
        postgresql_where=sa.text("vendor_key IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # procurement_case_lines
    # ------------------------------------------------------------------
    op.create_table(
        "procurement_case_lines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("procurement_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "line_number",
            sa.Integer(),
            nullable=False,
            comment="Display order within the case. Stable across re-scores of the same digest.",
        ),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
            comment="The invoice's description where one exists, else the PO's, else the receipt's.",
        ),
        sa.Column("sku", sa.String(length=128), nullable=True),
        # ---- the three sides -------------------------------------------
        sa.Column("po_line_index", sa.Integer(), nullable=True),
        sa.Column("po_quantity", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("po_unit_price_micros", sa.BigInteger(), nullable=True),
        sa.Column("po_amount_micros", sa.BigInteger(), nullable=True),
        sa.Column("receipt_line_index", sa.Integer(), nullable=True),
        sa.Column("receipt_quantity", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("invoice_line_index", sa.Integer(), nullable=True),
        sa.Column("invoice_quantity", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("invoice_unit_price_micros", sa.BigInteger(), nullable=True),
        sa.Column("invoice_amount_micros", sa.BigInteger(), nullable=True),
        # ---- the decision ----------------------------------------------
        sa.Column(
            "pair_cost",
            sa.Integer(),
            nullable=True,
            comment=(
                "The assignment cost that paired these lines, 0..1000000. "
                "NULL on an unpaired line. Stored so a reviewer can see HOW "
                "confident the pairing was, not merely that one happened."
            ),
        ),
        sa.Column(
            "price_delta_micros",
            sa.BigInteger(),
            nullable=True,
            comment="Signed: invoice minus PO. Positive means overcharged.",
        ),
        sa.Column(
            "quantity_delta",
            sa.Numeric(precision=20, scale=6),
            nullable=True,
            comment="Signed: invoice minus received. Positive means invoiced for more than arrived.",
        ),
        sa.Column(
            "findings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment=(
                "Every finding for this line, not only the one that won the "
                "outcome column. `outcome` is a single sort key for the "
                "queue; a line can be both over-priced and under-delivered "
                "and the reviewer needs both."
            ),
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment=(
                "Evidence pointers per side, in the Step 0 shape: "
                "{side: {work_item_id, page, char_start, char_end, bbox}}. "
                "This is what the comparison grid opens the PDF viewer with."
            ),
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
    )

    op.create_check_constraint(
        "ck_procurement_case_lines_outcome",
        "procurement_case_lines",
        f"outcome IN ({_quoted(LINE_OUTCOMES)})",
    )
    op.create_check_constraint(
        "ck_procurement_case_lines_pair_cost_range",
        "procurement_case_lines",
        "pair_cost IS NULL OR (pair_cost >= 0 AND pair_cost <= 1000000)",
    )
    # The outcome must be consistent with which sides are present, or the
    # queue's filters lie. NOT_ORDERED means no PO line; NOT_INVOICED means
    # no invoice line; NOT_RECEIVED means no receipt line. Checking the
    # presence facts at the database keeps a service bug from producing a
    # row that reads as an exception nobody can explain.
    op.create_check_constraint(
        "ck_procurement_case_lines_outcome_matches_sides",
        "procurement_case_lines",
        "(outcome <> 'NOT_ORDERED' OR po_line_index IS NULL) AND "
        "(outcome <> 'NOT_INVOICED' OR invoice_line_index IS NULL) AND "
        "(outcome <> 'NOT_RECEIVED' OR receipt_line_index IS NULL) AND "
        "(outcome <> 'MATCHED' OR invoice_line_index IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_procurement_case_lines_has_a_side",
        "procurement_case_lines",
        "po_line_index IS NOT NULL OR receipt_line_index IS NOT NULL "
        "OR invoice_line_index IS NOT NULL",
    )

    op.create_unique_constraint(
        "uq_procurement_case_lines_number",
        "procurement_case_lines",
        ["case_id", "line_number"],
    )
    op.create_index(
        "ix_procurement_case_lines_case",
        "procurement_case_lines",
        ["case_id", "line_number"],
    )
    op.create_index(
        "ix_procurement_case_lines_exceptions",
        "procurement_case_lines",
        ["workspace_id", "outcome"],
        postgresql_where=sa.text("outcome <> 'MATCHED'"),
    )
    op.create_index(
        "ix_procurement_case_lines_organization",
        "procurement_case_lines",
        ["organization_id"],
    )

    # ------------------------------------------------------------------
    # updated_at touch triggers
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION procurement_touch_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in (
        "procurement_cases",
        "procurement_case_lines",
        "procurement_tolerance_policies",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_touch_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION procurement_touch_updated_at();
            """
        )

    # ------------------------------------------------------------------
    # Published-policy immutability
    #
    # BEFORE UPDATE OR DELETE, and it inspects OLD. A DRAFT row being
    # published has OLD.status = 'DRAFT' and passes; every write afterwards
    # has OLD.status = 'PUBLISHED' and is refused.
    #
    # The message names the remedy, because the person who hits this is
    # editing a tolerance in a console and needs to be told that the way to
    # change a published tolerance is to publish the next version.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION procurement_tolerance_policy_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.status = 'PUBLISHED' THEN
                RAISE EXCEPTION
                    'procurement_tolerance_policies row % is PUBLISHED and '
                    'cannot be modified or deleted. Every case stamped with '
                    'policy_version %:% was scored under these numbers; '
                    'changing them would silently re-interpret those cases. '
                    'Publish a new version instead.',
                    OLD.id, OLD.id, OLD.version
                    USING ERRCODE = 'restrict_violation';
            END IF;
            -- A BEFORE DELETE trigger that returns NEW returns NULL, which
            -- CANCELS the delete. A draft must stay deletable, so the two
            -- operations return different rows.
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_procurement_tolerance_policies_immutable
        BEFORE UPDATE OR DELETE ON procurement_tolerance_policies
        FOR EACH ROW
        EXECUTE FUNCTION procurement_tolerance_policy_immutable();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_procurement_tolerance_policies_immutable "
        "ON procurement_tolerance_policies"
    )
    op.execute("DROP FUNCTION IF EXISTS procurement_tolerance_policy_immutable()")
    for table in (
        "procurement_cases",
        "procurement_case_lines",
        "procurement_tolerance_policies",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_touch_updated_at ON {table}")
    op.execute("DROP FUNCTION IF EXISTS procurement_touch_updated_at()")
    op.drop_table("procurement_case_lines")
    op.drop_table("procurement_cases")
    op.drop_table("procurement_tolerance_policies")