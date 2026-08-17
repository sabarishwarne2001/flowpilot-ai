"""ARCH-10 Step 3a — audit enum values for spend refusals (EXPAND)

Revision ID: arch10_step3_audit_enum
Revises: arch10_step2_usage_expand
Create Date: 2026-08-17

`ALTER TYPE ... ADD VALUE` cannot run inside a transaction block, which is the
friction ARCH-06 Step 6 documented for `SessionRevokedReason` and ARCH-07 §B.1
chose the split-enum design to minimise. Both values are added in this one
revision so the friction is paid once, per the ARCH-08 precedent.

`autocommit_block()` means this revision is **not** transactionally reversible.
`downgrade()` is a documented no-op: PostgreSQL has no `ALTER TYPE ... DROP
VALUE`, and rebuilding both enums to remove two members would require
rewriting every `audit_logs` row — a far worse outcome than two unused enum
members. This is recorded rather than silently omitted.
"""

from __future__ import annotations

from alembic import op

revision = "arch10_step3_audit_enum"
down_revision = "arch10_step2_usage_expand"
branch_labels = None
depends_on = None

NEW_RESOURCE_TYPES: tuple[str, ...] = ("SPEND_LIMIT",)
NEW_ACTIONS: tuple[str, ...] = ("EXCEEDED",)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in NEW_RESOURCE_TYPES:
            op.execute(
                f"ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS '{value}'"
            )
        for value in NEW_ACTIONS:
            op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Intentionally a no-op. See the module docstring.
    pass