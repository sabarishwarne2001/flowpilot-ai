"""ARCH-37 Step 0 — audit vocabulary for the flow builder (EXPAND)

Revision ID: arch37_step0_flow_vocabulary
Revises: arch39_step1_conversations
Create Date: 2026-09-17

WHY THIS IS ITS OWN MIGRATION
=============================

`arch37_step1_flow_builder` pauses rules whose trigger could never fire and
writes one audit row per paused rule. Those rows need the resource type
AUTOMATION_RULE, and PostgreSQL refuses to let an enum value be used in the
transaction that added it. ARCH-20, 22, 26 and 31 split vocabulary from DDL for
the same reason; this follows them.

AUTOMATION_RULE is also what the rule API now records on create, update and
delete. Before ARCH-37 a rule that could send email to any address was edited
with no audit trail at all.
"""

from __future__ import annotations

from alembic import op

revision = "arch37_step0_flow_vocabulary"
down_revision = "arch39_step1_conversations"
branch_labels = None
depends_on = None

NEW_RESOURCE_TYPES: tuple[str, ...] = ("AUTOMATION_RULE",)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in NEW_RESOURCE_TYPES:
            op.execute(
                f"ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS '{value}'"
            )


def downgrade() -> None:
    """No-op, deliberately.

    PostgreSQL cannot drop an enum value, and rewriting `audit_resource_type`
    would rewrite every audit row, which ARCH-07's immutability trigger
    refuses. A value nothing emits is harmless.
    """
