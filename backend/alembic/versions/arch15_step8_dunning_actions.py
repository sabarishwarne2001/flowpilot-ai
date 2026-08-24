"""ARCH-15 Step 15.8 — dunning_actions (EXPAND)

Revision ID: arch15_step8_dunning_actions
Revises: arch15_step7_audit_enum
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch15_step8_dunning_actions"
down_revision = "arch15_step7_audit_enum"
branch_labels = None
depends_on = None

DUNNING_STEP_ENUM = "dunning_step"

DUNNING_STEP_VALUES: tuple[str, ...] = (
    "NOTIFY_1",
    "NOTIFY_2",
    "NOTIFY_3",
    "RESTRICT_WRITES",
    "SUSPEND_WRITES",
)

DUNNING_OUTCOME_ENUM = "dunning_outcome"
DUNNING_OUTCOME_VALUES: tuple[str, ...] = ("APPLIED", "SKIPPED", "FAILED")


def upgrade() -> None:
    postgresql.ENUM(*DUNNING_STEP_VALUES, name=DUNNING_STEP_ENUM).create(
        op.get_bind(), checkfirst=True
    )
    postgresql.ENUM(*DUNNING_OUTCOME_VALUES, name=DUNNING_OUTCOME_ENUM).create(
        op.get_bind(), checkfirst=True
    )

    op.create_table(
        "dunning_actions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "step",
            postgresql.ENUM(
                *DUNNING_STEP_VALUES, name=DUNNING_STEP_ENUM, create_type=False
            ),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            postgresql.ENUM(
                *DUNNING_OUTCOME_VALUES,
                name=DUNNING_OUTCOME_ENUM,
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text(f"'APPLIED'::{DUNNING_OUTCOME_ENUM}"),
        ),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("stripe_event_id", sa.Text(), nullable=True),
        sa.Column("notified_user_count", sa.Integer(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            name="fk_dunning_actions_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.id"],
            name="fk_dunning_actions_subscription_id_subscriptions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_dunning_actions_invoice_id_invoices",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "subscription_id",
            "invoice_id",
            "step",
            name="uq_dunning_actions_subscription_invoice_step",
        ),
    )

    op.create_index(
        "ix_dunning_actions_organization_id", "dunning_actions", ["organization_id"]
    )
    op.create_index("ix_dunning_actions_invoice_id", "dunning_actions", ["invoice_id"])
    op.execute(
        "CREATE INDEX ix_dunning_actions_applied "
        "ON dunning_actions (applied_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_dunning_actions_restrictive "
        "ON dunning_actions (organization_id, applied_at DESC) "
        f"WHERE step IN ('RESTRICT_WRITES'::{DUNNING_STEP_ENUM}, "
        f"'SUSPEND_WRITES'::{DUNNING_STEP_ENUM}) "
        f"AND outcome = 'APPLIED'::{DUNNING_OUTCOME_ENUM}"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_dunning_actions_restrictive")
    op.execute("DROP INDEX IF EXISTS ix_dunning_actions_applied")
    op.drop_table("dunning_actions")
    postgresql.ENUM(name=DUNNING_OUTCOME_ENUM).drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name=DUNNING_STEP_ENUM).drop(op.get_bind(), checkfirst=True)