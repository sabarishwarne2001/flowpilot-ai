"""ARCH-15 Step 15.7a — audit enum values for billing (EXPAND)

Revision ID: arch15_step7_audit_enum
Revises: arch15_step5_invoices
Create Date: 2026-08-24
"""

from __future__ import annotations

from alembic import op

revision = "arch15_step7_audit_enum"
down_revision = "arch15_step5_invoices"
branch_labels = None
depends_on = None

NEW_RESOURCE_TYPES: tuple[str, ...] = (
    "BILLING_ACCOUNT",
    "SUBSCRIPTION",
    "INVOICE",
)

NEW_ACTIONS: tuple[str, ...] = (
    "PORTAL_SESSION_MINTED",
    "CHECKOUT_STARTED",
    "SEATS_CHANGED",
    "DUNNING_STEP_APPLIED",
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in NEW_RESOURCE_TYPES:
            op.execute(
                f"ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS '{value}'"
            )
        for value in NEW_ACTIONS:
            op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    pass
