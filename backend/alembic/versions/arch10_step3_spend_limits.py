"""ARCH-10 Step 3b — spend_limits (EXPAND)

Revision ID: arch10_step3_spend_limits
Revises: arch10_step3_audit_enum
Create Date: 2026-08-17

New and empty, so all constraints apply from row zero.

No backfill: an organization with no row deliberately inherits the platform
defaults from `settings`. Seeding every existing org with an explicit row would
freeze today's default into the database and make raising the platform ceiling
a data migration instead of a config change.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch10_step3_spend_limits"
down_revision = "arch10_step3_audit_enum"
branch_labels = None
depends_on = None

PERIOD_VALUES: tuple[str, ...] = ("DAY", "MONTH")


def upgrade() -> None:
    bind = op.get_bind()

    period = postgresql.ENUM(*PERIOD_VALUES, name="spend_limit_period")
    period.create(bind, checkfirst=True)

    op.create_table(
        "spend_limits",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("limit_key", sa.String(length=64), nullable=False),
        sa.Column(
            "period",
            postgresql.ENUM(
                *PERIOD_VALUES, name="spend_limit_period", create_type=False
            ),
            nullable=False,
            server_default=sa.text("'MONTH'::spend_limit_period"),
        ),
        sa.Column("max_quantity", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("max_cost_micros", sa.BigInteger(), nullable=True),
        sa.Column(
            "hard_stop", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("note", sa.String(length=500), nullable=True),
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
            name="fk_spend_limits_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "max_quantity IS NOT NULL OR max_cost_micros IS NOT NULL",
            name="ck_spend_limits_at_least_one_ceiling",
        ),
        sa.CheckConstraint(
            "max_quantity IS NULL OR max_quantity >= 0",
            name="ck_spend_limits_quantity_non_negative",
        ),
        sa.CheckConstraint(
            "max_cost_micros IS NULL OR max_cost_micros >= 0",
            name="ck_spend_limits_cost_non_negative",
        ),
        sa.CheckConstraint(
            "length(limit_key) > 0", name="ck_spend_limits_limit_key_not_blank"
        ),
    )

    op.execute(
        "CREATE UNIQUE INDEX uq_spend_limits_active_key "
        "ON spend_limits (organization_id, limit_key, period) WHERE is_active"
    )
    op.execute(
        "CREATE INDEX ix_spend_limits_organization_id "
        "ON spend_limits (organization_id) WHERE is_active"
    )


def downgrade() -> None:
    op.drop_table("spend_limits")
    op.execute("DROP TYPE IF EXISTS spend_limit_period")