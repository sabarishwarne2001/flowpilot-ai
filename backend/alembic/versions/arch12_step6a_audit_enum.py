"""ARCH-12 Step 6a — audit enum values for generation provenance (EXPAND)

Revision ID: arch12_step6a_audit_enum
Revises: arch12_step1_stream_state
Create Date: 2026-08-20

`ALTER TYPE ... ADD VALUE` cannot run inside a transaction block. This is the
same friction ARCH-06 Step 6 documented and ARCH-10 Step 3a paid for the spend
enums; both values are added in this one revision so it is paid once, per the
ARCH-08 precedent.

CONVERSATION is a new `audit_resource_type` because a generation is not a
WORKSPACE event and not a WORK_ITEM event — it is an event about one
conversation turn, and ARCH-14's usage API and ARCH-18's export both need to
be able to select it without a LIKE over `details`.

GENERATED is a new `audit_action` for the same reason ACCESSED exists
separately from UPDATED: "the model produced an answer from this exact sealed
context at this timestamp" is the claim the enterprise moat rests on, and
overloading CREATED would make it indistinguishable from the row that records
the conversation being opened.

`autocommit_block()` means this revision is not transactionally reversible.
`downgrade()` is a documented no-op: PostgreSQL has no `ALTER TYPE ... DROP
VALUE`, and rebuilding both enums to remove two members would require
rewriting every `audit_logs` row — which ARCH-07 Step 4 made immutable at the
trigger level, so the rewrite would fail anyway.
"""

from __future__ import annotations

from alembic import op

revision = "arch12_step6a_audit_enum"
down_revision = "arch12_step1_stream_state"
branch_labels = None
depends_on = None

NEW_RESOURCE_TYPES: tuple[str, ...] = ("CONVERSATION",)
NEW_ACTIONS: tuple[str, ...] = ("GENERATED",)


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
