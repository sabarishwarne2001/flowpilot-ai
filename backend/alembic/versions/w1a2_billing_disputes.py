"""Chargebacks on an organization's payments (website and payments session, C5).

`billing_disputes` records what Dodo Payments reports about a dispute on a payment, and whether a
lost dispute (a reversed payment) still holds the organization read-only. The operator alert kinds
gain PAYMENT_DISPUTED and PAYMENT_REVERSED.

Revision ID: w1a2_billing_disputes
Revises: w1a1_servabo_database_names
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "w1a2_billing_disputes"
down_revision: Union[str, None] = "w1a1_servabo_database_names"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_KINDS_BEFORE = ("DEAD_JOBS", "DEAD_GATEWAY_EVENTS", "SERVER_ERRORS", "DUPLICATE_SUBSCRIPTION")
_KINDS_AFTER = _KINDS_BEFORE + ("PAYMENT_DISPUTED", "PAYMENT_REVERSED")


def _alert_kinds(kinds: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_operator_alerts_kind_known"), "operator_alerts", type_="check")
    op.create_check_constraint(
        op.f("ck_operator_alerts_kind_known"), "operator_alerts", "kind IN (" + ", ".join(f"'{k}'" for k in kinds) + ")"
    )


def upgrade() -> None:
    _alert_kinds(_KINDS_AFTER)
    op.create_table(
        "billing_disputes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("gateway", sa.String(16), nullable=False),
        sa.Column("gateway_dispute_id", sa.String(255), nullable=False),
        sa.Column("gateway_payment_id", sa.String(255), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("amount_micros", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("cleared_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("gateway", "gateway_dispute_id", name="uq_billing_disputes_gateway_dispute"),
        sa.CheckConstraint("gateway IN ('STRIPE', 'DODO')", name="gateway_known"),
    )
    op.create_index("ix_billing_disputes_organization", "billing_disputes", ["organization_id", "created_at"])
    op.create_index("ix_billing_disputes_reversed_open", "billing_disputes", ["organization_id"],
                    postgresql_where=sa.text("reversed_at IS NOT NULL AND cleared_at IS NULL"))


def downgrade() -> None:
    op.execute("DELETE FROM operator_alerts WHERE kind IN ('PAYMENT_DISPUTED', 'PAYMENT_REVERSED')")
    _alert_kinds(_KINDS_BEFORE)
    op.drop_index("ix_billing_disputes_reversed_open", table_name="billing_disputes")
    op.drop_index("ix_billing_disputes_organization", table_name="billing_disputes")
    op.drop_table("billing_disputes")
