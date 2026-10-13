"""Campaign session 3 — notification email preferences and once-per-period limit alerts.

Revision ID: s3a3_notification_preferences
Revises: s3a2_require_two_factor
Create Date: 2026-10-12

`user_notification_preferences` holds a person's choice for each OPTIONAL email category they
changed (no row: the default, on). `usage_limit_alerts` records each plan-limit alert (80% and
100%, per meter, limit and billing period; a plan change is a new limit and re-arms them) before
it is sent, so it goes out once.

Downgrade drops both tables.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "s3a3_notification_preferences"
down_revision = "s3a2_require_two_factor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_notification_preferences",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "category", name="pk_user_notification_preferences"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE",
                                name="fk_user_notification_preferences_user_id_users"),
    )
    op.create_table(
        "usage_limit_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("limit_key", sa.String(64), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("ceiling", sa.BigInteger(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_usage_limit_alerts"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE",
                                name="fk_usage_limit_alerts_organization_id_organizations"),
        sa.UniqueConstraint("organization_id", "limit_key", "level", "ceiling", "period_start",
                            name="uq_usage_limit_alerts_once_per_period"),
    )


def downgrade() -> None:
    op.drop_table("usage_limit_alerts")
    op.drop_table("user_notification_preferences")
