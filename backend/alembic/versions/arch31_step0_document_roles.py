"""ARCH-31 Step 0 — document_roles: what each document IS, before anything tries to match it.

Revision ID: arch31_step0_document_roles
Revises: arch30_step3_workspace_clock
Create Date: 2026-09-12

WHY A TABLE AND NOT A COLUMN ON work_items
==========================================

`work_items` already carries `extracted_entities` and `extraction_metadata`,
and the obvious cheap move would be a `role` column beside them. Three reasons
this is a table instead:

1. A role has provenance. CLASSIFIER, RULE and USER are different claims with
   different authority, and the whole correctness of ARCH-31's override
   behaviour is that a USER role is never overwritten by a re-run. A column
   would need a second column for the source and a third for confidence, and
   at that point it is a table wearing a disguise.

2. The normalised header fields live with it. `vendor_key`, `document_number`,
   `document_date`, `currency` and `total_micros` are derived from the document
   by `app/core/normalize.py`, and the matcher reads them thousands of times
   per case. Putting them here gives the candidate-discovery query an index to
   use instead of a JSONB path scan over every work item in the workspace.

3. Re-extraction. When a document is re-OCR'd the entities change; the human's
   role decision must not. Separating the row makes "keep the role, replace the
   derived fields" a one-line UPDATE instead of a careful JSONB merge.

EXPAND-SHAPED
=============

New table only. Nothing existing is altered, so this migration is safe to run
before the code that reads it ships, and safe to leave in place if ARCH-31 is
rolled back — an unused table costs nothing.

ISOLATION
=========

`organization_id` AND `workspace_id`, both NOT NULL, per ARCH-02. The
composite index leads with workspace because every query the matcher issues is
workspace-scoped; the organization column exists so that a cross-tenant bug
produces an FK violation rather than a leak.

THE UNIQUE CONSTRAINT IS THE INTERESTING ONE
============================================

One role row per work item. Not per (work_item, role) — a document is one
thing, and allowing two rows would let a re-classification land beside the
human's decision rather than being refused by it. The USER-override rule is
enforced in the service, but the constraint is what makes the rule
enforceable: with two rows permitted, "did a human decide?" stops having a
single answer.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch31_step0_document_roles"
down_revision = "arch30_step3_workspace_clock"
branch_labels = None
depends_on = None

ROLES = (
    "INVOICE",
    "PURCHASE_ORDER",
    "GOODS_RECEIPT",
    "CREDIT_NOTE",
    "CONTRACT",
    "STATEMENT",
    "OTHER",
)
ROLE_SOURCES = ("CLASSIFIER", "USER", "RULE")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "document_roles",
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
            "work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "role_source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'CLASSIFIER'"),
        ),
        sa.Column(
            "role_confidence",
            sa.Numeric(precision=4, scale=3),
            nullable=True,
            comment=(
                "0.000..1.000 from the deterministic classifier. NULL when "
                "role_source is USER: a human did not estimate anything, and "
                "storing 1.000 would let a later rule 'beat' them on score."
            ),
        ),
        # ---- normalised header, from app/core/normalize.py ----------------
        sa.Column("vendor_key", sa.String(length=255), nullable=True),
        sa.Column("document_number", sa.String(length=128), nullable=True),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column(
            "total_micros",
            sa.BigInteger(),
            nullable=True,
            comment=(
                "Integer millionths, the codebase-wide money representation. "
                "Signed: a credit note is negative."
            ),
        ),
        sa.Column(
            "normalization_warnings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment=(
                "Ambiguities the normalizer resolved from workspace settings "
                "rather than from the page, e.g. a currency taken from "
                "workspaces.currency. Surfaced as evidence so a reviewer can "
                "see that the document did not actually say it."
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
        "ck_document_roles_role", "document_roles", f"role IN ({_quoted(ROLES)})"
    )
    op.create_check_constraint(
        "ck_document_roles_role_source",
        "document_roles",
        f"role_source IN ({_quoted(ROLE_SOURCES)})",
    )
    op.create_check_constraint(
        "ck_document_roles_confidence_range",
        "document_roles",
        "role_confidence IS NULL OR "
        "(role_confidence >= 0 AND role_confidence <= 1)",
    )
    # A human decision carries no machine confidence. Without this a rule
    # could write role_source='USER' with a confidence and later code could
    # compare that score against its own and win.
    op.create_check_constraint(
        "ck_document_roles_user_has_no_confidence",
        "document_roles",
        "role_source <> 'USER' OR role_confidence IS NULL",
    )
    op.create_check_constraint(
        "ck_document_roles_currency_shape",
        "document_roles",
        "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
    )

    op.create_unique_constraint(
        "uq_document_roles_work_item", "document_roles", ["work_item_id"]
    )

    # Candidate discovery: "other documents from this vendor in this workspace
    # near this date". Leads with workspace because every query is scoped to
    # one, then vendor, then date for the window scan.
    op.create_index(
        "ix_document_roles_candidate_window",
        "document_roles",
        ["workspace_id", "vendor_key", "document_date"],
        postgresql_where=sa.text("vendor_key IS NOT NULL"),
    )
    # PO-reference lookup, the fast path before the vendor window is opened.
    op.create_index(
        "ix_document_roles_number",
        "document_roles",
        ["workspace_id", "document_number"],
        postgresql_where=sa.text("document_number IS NOT NULL"),
    )
    op.create_index(
        "ix_document_roles_role",
        "document_roles",
        ["workspace_id", "role"],
    )
    op.create_index(
        "ix_document_roles_organization",
        "document_roles",
        ["organization_id"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION document_roles_touch_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_document_roles_touch_updated_at
        BEFORE UPDATE ON document_roles
        FOR EACH ROW
        EXECUTE FUNCTION document_roles_touch_updated_at();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_document_roles_touch_updated_at "
        "ON document_roles"
    )
    op.execute("DROP FUNCTION IF EXISTS document_roles_touch_updated_at()")
    op.drop_table("document_roles")