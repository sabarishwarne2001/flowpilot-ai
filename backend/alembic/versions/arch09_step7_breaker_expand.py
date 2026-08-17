"""ARCH-09 Step 7a — circuit breaker counters (EXPAND)

Revision ID: arch09_step7_breaker_expand
Revises: arch09_step6_attempts_expand
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "arch09_step7_breaker_expand"
down_revision = "arch09_step6_attempts_expand"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS 'WEBHOOK_ENDPOINT'"
        )
        op.execute(
            "ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'WEBHOOK_ENDPOINT_AUTO_DISABLED'"
        )

    op.add_column(
        "webhook_endpoints",
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "webhook_endpoints",
        sa.Column("first_failure_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "webhook_endpoints",
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "webhook_endpoints",
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "webhook_endpoints",
        sa.Column(
            "auto_disabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.create_check_constraint(
        "consecutive_failures_non_negative",
        "webhook_endpoints",
        "consecutive_failures >= 0",
    )
    op.create_check_constraint(
        "failure_streak_consistent",
        "webhook_endpoints",
        "(consecutive_failures = 0) = (first_failure_at IS NULL)",
    )
    op.create_check_constraint(
        "auto_disabled_implies_disabled",
        "webhook_endpoints",
        "NOT auto_disabled OR status = 'DISABLED'::webhook_endpoint_status",
    )

    op.create_index(
        "ix_webhook_endpoints_auto_disabled",
        "webhook_endpoints",
        ["organization_id", sa.text("disabled_at DESC")],
        postgresql_where=sa.text("auto_disabled"),
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_endpoints_auto_disabled", table_name="webhook_endpoints")
    op.drop_constraint(
        "ck_webhook_endpoints_auto_disabled_implies_disabled",
        "webhook_endpoints",
        type_="check",
    )
    op.drop_constraint(
        "ck_webhook_endpoints_failure_streak_consistent",
        "webhook_endpoints",
        type_="check",
    )
    op.drop_constraint(
        "ck_webhook_endpoints_consecutive_failures_non_negative",
        "webhook_endpoints",
        type_="check",
    )
    op.drop_column("webhook_endpoints", "auto_disabled")
    op.drop_column("webhook_endpoints", "last_success_at")
    op.drop_column("webhook_endpoints", "last_failure_at")
    op.drop_column("webhook_endpoints", "first_failure_at")
    op.drop_column("webhook_endpoints", "consecutive_failures")