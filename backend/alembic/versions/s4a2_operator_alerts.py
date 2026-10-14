"""Campaign session 4 — alerts for the deployment's owner (§C.4).

Revision ID: s4a2_operator_alerts
Revises: s4a1_invoice_refunds
Create Date: 2026-10-13

`operator_alerts` (one row per thing worth telling the owner: dead jobs of a type, gateway events
that will never apply, a burst of server errors, a customer with two live gateway subscriptions),
and `server_error_minutes` (5xx answers per minute, summed across API processes).

Downgrade drops both tables.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "s4a2_operator_alerts"
down_revision = "s4a1_invoice_refunds"
branch_labels = None
depends_on = None

KINDS = ("DEAD_JOBS", "DEAD_GATEWAY_EVENTS", "SERVER_ERRORS", "DUPLICATE_SUBSCRIPTION")


def upgrade() -> None:
    op.create_table(
        "operator_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_emailed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("emailed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_operator_alerts"),
        sa.UniqueConstraint("dedupe_key", name="uq_operator_alerts_dedupe_key"),
        sa.CheckConstraint("kind IN (" + ", ".join(f"'{k}'" for k in KINDS) + ")", name="kind_known"),
        sa.CheckConstraint("occurrences >= 1", name="occurrences_positive"),
    )
    op.create_table(
        "server_error_minutes",
        sa.Column("minute", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("minute", name="pk_server_error_minutes"),
    )


def downgrade() -> None:
    op.drop_table("server_error_minutes")
    op.drop_table("operator_alerts")
