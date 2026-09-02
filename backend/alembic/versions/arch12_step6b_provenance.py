"""ARCH-12 Step 6b — citation provenance columns (EXPAND)

Revision ID: arch12_step6b_provenance
Revises: arch12_step6a_audit_enum
Create Date: 2026-08-20

`context_hash` and `audit_log_id` are the two columns that turn "the AI said
so" into "here is the exact context the model saw, sealed in a tamper-evident
log at this timestamp". ARCH-07 built the log; this writes one row per
generation and stores the pointer back.

WHY THE POINTER LIVES ON THE MESSAGE AND NOT ONLY IN THE LOG
------------------------------------------------------------
The audit log is queried by organization and time. The click-to-cite frontend
is queried by message. Without this column, rendering one citation panel means
scanning the audit log for a row whose `details` mentions this message id —
which is a sequential scan of an append-only table that ARCH-17 is going to
partition precisely because it grows without bound. One nullable UUID column
here removes that read path entirely.

WHY `ondelete` IS NOT SET ON `audit_log_id`
--------------------------------------------
It is deliberately **not** a foreign key. ARCH-07 Step 4 made `audit_logs`
immutable and append-only; a real FK with any `ondelete` behaviour would
either block the erasure ARCH-18 owes GDPR subjects or silently NULL the
pointer that proves the generation happened. The column stores the id and the
join is made in application code, which is the same discipline
`usage_events.resource_id` already follows.

`context_hash` is CHAR-bounded rather than free text because it has exactly
one shape: `sha256:` plus 64 hex characters. The CHECK is safe to add
immediately — nothing writes the column before this migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "arch12_step6b_provenance"
down_revision = "arch12_step6a_audit_enum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column(
            "context_hash",
            sa.String(71),
            nullable=True,
            comment=(
                "ARCH-12 Step 6. 'sha256:' + 64 hex of the exact assembled "
                "context string handed to the provider. NULL on user rows and "
                "on assistant rows generated with no retrieved context."
            ),
        ),
    )

    op.add_column(
        "conversation_messages",
        sa.Column(
            "audit_log_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
            comment=(
                "ARCH-12 Step 6. Points at the audit_logs row sealing this "
                "generation. Deliberately not a foreign key — see the module "
                "docstring on the revision that added it."
            ),
        ),
    )

    op.create_check_constraint(
        "ck_conversation_messages_context_hash_shape",
        "conversation_messages",
        "context_hash IS NULL OR context_hash ~ '^sha256:[0-9a-f]{64}$'",
    )

    op.create_index(
        "ix_conversation_messages_audit_log_id",
        "conversation_messages",
        ["audit_log_id"],
        postgresql_where=sa.text("audit_log_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_messages_audit_log_id",
        table_name="conversation_messages",
    )
    op.drop_constraint(
        "ck_conversation_messages_context_hash_shape",
        "conversation_messages",
        type_="check",
    )
    op.drop_column("conversation_messages", "audit_log_id")
    op.drop_column("conversation_messages", "context_hash")
