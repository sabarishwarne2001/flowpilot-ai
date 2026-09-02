"""ARCH-20 Step 1 — audit vocabulary for governance, residency and erasure (EXPAND)

Revision ID: arch20_step1_audit_vocabulary
Revises: arch18_step1_cogs_margins
Create Date: 2026-08-31
"""

from __future__ import annotations

from alembic import op

revision = "arch20_step1_audit_vocabulary"
down_revision = "arch18_step1_cogs_margins"
branch_labels = None
depends_on = None

NEW_RESOURCE_TYPES: tuple[str, ...] = (
    "COMPLIANCE_EXPORT",
    "ERASED_SUBJECT",
    "RETENTION_POLICY",
    "DATA_RESIDENCY",
)

NEW_ACTIONS: tuple[str, ...] = (
    "ERASED",
    "RESIDENCY_CHANGED",
    "RETENTION_CHANGED",
    "EXPORT_REQUESTED",
    "EXPORT_COMPLETED",
    "PURGED",
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
