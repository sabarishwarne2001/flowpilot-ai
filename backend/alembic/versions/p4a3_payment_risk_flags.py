"""PHASE 4 — payment-risk flags (bank account changed, suspiciously round total).

Revision ID: p4a3_payment_risk_flags
Revises: p4a2_webhook_test_event
Create Date: 2026-10-05

A new table, not new kinds in anomaly_findings: ARCH-34 pins that table's
finding vocabulary in its migration and its verification gate. See
app/models/payment_risk.py.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "p4a3_payment_risk_flags"
down_revision = "p4a2_webhook_test_event"
branch_labels = None
depends_on = None

KINDS = ("BANK_ACCOUNT_CHANGED", "ROUND_AMOUNT")
SEVERITIES = ("LOW", "MEDIUM", "HIGH")
STATUSES = ("OPEN", "CONFIRMED", "DISMISSED")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_table(
        "payment_risk_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_item_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("work_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("counterpart_work_item_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("work_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="OPEN"),
        sa.Column("vendor_key", sa.String(300), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("work_item_id", "kind", name="uq_payment_risk_flags_item_kind"),
        sa.CheckConstraint(f"kind IN ({_in(KINDS)})", name="ck_payment_risk_flags_kind"),
        sa.CheckConstraint(f"severity IN ({_in(SEVERITIES)})", name="ck_payment_risk_flags_severity"),
        sa.CheckConstraint(f"status IN ({_in(STATUSES)})", name="ck_payment_risk_flags_status"),
        sa.CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_payment_risk_flags_details_object"),
    )
    op.create_index("ix_payment_risk_flags_workspace_status", "payment_risk_flags",
                    ["workspace_id", "status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_payment_risk_flags_workspace_status", table_name="payment_risk_flags")
    op.drop_table("payment_risk_flags")
