"""ARCH-13 Step 13.7 — document_verifications, document_verification_fields

Revision ID: arch13_step7_verification
Revises: arch13_step4_automation_graph
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch13_step7_verification"
down_revision = "arch13_step4_automation_graph"
branch_labels = None
depends_on = None


VERIFICATION_STATUS_ENUM = "document_verification_status"
DISAGREEMENT_KIND_ENUM = "document_disagreement_kind"

VERIFICATION_STATUSES = (
    "PENDING",
    "AGREED",
    "DISAGREED",
    "REVIEWED",
    "AUTO_APPROVED",
)

DISAGREEMENT_KINDS = ("MISSING", "CONFLICT", "FORMAT")

TERMINAL_SQL = "'AGREED', 'DISAGREED', 'REVIEWED', 'AUTO_APPROVED'"


def upgrade() -> None:
    status_enum = postgresql.ENUM(
        *VERIFICATION_STATUSES, name=VERIFICATION_STATUS_ENUM, create_type=False
    )
    kind_enum = postgresql.ENUM(
        *DISAGREEMENT_KINDS, name=DISAGREEMENT_KIND_ENUM, create_type=False
    )
    status_enum.create(op.get_bind(), checkfirst=True)
    kind_enum.create(op.get_bind(), checkfirst=True)

    # ---- the opt-in --------------------------------------------------
    op.add_column(
        "document_settings",
        sa.Column(
            "verification_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "document_settings",
        sa.Column("verification_agents", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "verification_agents_bounded",
        "document_settings",
        "verification_agents IS NULL OR "
        "(verification_agents >= 2 AND verification_agents <= 5)",
    )

    # ---- document_verifications --------------------------------------
    op.create_table(
        "document_verifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("agent_count", sa.Integer(), nullable=False),
        sa.Column("agreement_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column(
            "cost_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "auto_approved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "reviewed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
        sa.CheckConstraint(
            "agent_count >= 2 AND agent_count <= 5",
            name="ck_document_verifications_agent_count_bounded",
        ),
        sa.CheckConstraint(
            "agreement_score IS NULL OR "
            "(agreement_score >= 0 AND agreement_score <= 1)",
            name="ck_document_verifications_agreement_score_ratio",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_document_verifications_confidence_ratio",
        ),
        sa.CheckConstraint(
            "cost_micros >= 0", name="ck_document_verifications_cost_non_negative"
        ),
        sa.CheckConstraint(
            "(status = 'REVIEWED'::document_verification_status) = "
            "(reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_document_verifications_reviewer_matches_status",
        ),
        sa.CheckConstraint(
            "NOT (auto_approved AND reviewed_by_user_id IS NOT NULL)",
            name="ck_document_verifications_auto_approved_has_no_reviewer",
        ),
        sa.CheckConstraint(
            f"(status IN ({TERMINAL_SQL})) = (agreement_score IS NOT NULL)",
            name="ck_document_verifications_score_matches_status",
        ),
    )

    op.create_index(
        "uq_document_verifications_open_work_item",
        "document_verifications",
        ["work_item_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('PENDING'::document_verification_status, "
            "'DISAGREED'::document_verification_status)"
        ),
    )

    op.create_index(
        "ix_document_verifications_workspace_status",
        "document_verifications",
        ["workspace_id", "status", sa.text("created_at DESC")],
    )

    op.create_index(
        "ix_document_verifications_pending",
        "document_verifications",
        ["created_at"],
        postgresql_where=sa.text(
            "status = 'PENDING'::document_verification_status"
        ),
    )

    # ---- document_verification_fields --------------------------------
    op.create_table(
        "document_verification_fields",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "verification_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_verifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_path", sa.String(length=200), nullable=False),
        sa.Column("agreed", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("consensus_value", postgresql.JSONB(), nullable=True),
        sa.Column(
            "agent_values",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("disagreement_kind", kind_enum, nullable=True),
        sa.Column("resolved_value", postgresql.JSONB(), nullable=True),
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
            "confidence >= 0 AND confidence <= 1",
            name="ck_document_verification_fields_confidence_ratio",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(agent_values) = 'array'",
            name="ck_document_verification_fields_agent_values_is_array",
        ),
        sa.CheckConstraint(
            "agreed = (disagreement_kind IS NULL)",
            name="ck_document_verification_fields_kind_matches_agreed",
        ),
        sa.UniqueConstraint(
            "verification_id",
            "field_path",
            name="uq_document_verification_fields_verification_field",
        ),
    )

    op.create_index(
        "ix_document_verification_fields_disagreed",
        "document_verification_fields",
        ["verification_id"],
        postgresql_where=sa.text("agreed = false"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_verification_fields_disagreed",
        table_name="document_verification_fields",
    )
    op.drop_table("document_verification_fields")

    op.drop_index(
        "ix_document_verifications_pending", table_name="document_verifications"
    )
    op.drop_index(
        "ix_document_verifications_workspace_status",
        table_name="document_verifications",
    )
    op.drop_index(
        "uq_document_verifications_open_work_item",
        table_name="document_verifications",
    )
    op.drop_table("document_verifications")

    op.execute(
        """
        DO $$
        DECLARE
            r RECORD;
        BEGIN
            FOR r IN (
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'document_settings'::regclass
                  AND conname LIKE '%verification_agents%'
            ) LOOP
                EXECUTE 'ALTER TABLE document_settings DROP CONSTRAINT IF EXISTS ' || quote_ident(r.conname);
            END LOOP;
        END $$;
        """
    )
    op.drop_column("document_settings", "verification_agents")
    op.drop_column("document_settings", "verification_enabled")

    postgresql.ENUM(name=DISAGREEMENT_KIND_ENUM).drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name=VERIFICATION_STATUS_ENUM).drop(op.get_bind(), checkfirst=True)
