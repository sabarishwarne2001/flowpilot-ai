"""Campaign session 4 — an invoice records the money refunded on it (N-069).

Revision ID: s4a1_invoice_refunds
Revises: s3a5_review_assignment_effective_access
Create Date: 2026-10-13

`invoices.amount_refunded_micros` (how much of the payment the gateway has given back, never more
than was paid) and `invoices.refunded_at` (when the latest refund was recorded). Both stay writable
after an invoice is finalized: the immutability trigger freezes the amounts that were invoiced, not
what happened to the payment afterwards, exactly as for `amount_paid_micros`.

Downgrade drops the check and both columns.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "s4a1_invoice_refunds"
down_revision = "s3a5_review_assignment_effective_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("amount_refunded_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("invoices", sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_invoices_refund_within_payment",
        "invoices",
        "amount_refunded_micros >= 0 AND amount_refunded_micros <= amount_paid_micros",
    )


def downgrade() -> None:
    op.drop_constraint("ck_invoices_refund_within_payment", "invoices", type_="check")
    op.drop_column("invoices", "refunded_at")
    op.drop_column("invoices", "amount_refunded_micros")
