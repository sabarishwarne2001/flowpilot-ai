"""ARCH-12 Step 1 — streaming settlement substrate (EXPAND)

Revision ID: arch12_step1_stream_state
Revises: arch11_5_intent_config
Create Date: 2026-08-20

WHY THESE COLUMNS EXIST
-----------------------
A synchronous generation has exactly two states the schema needs to know
about: it happened, or the request failed and no row was written. SSE adds
three that the current `conversation_messages` shape cannot represent at all:

  1. Abandoned mid-stream — tokens were generated and billed by the provider,
     the client vanished, and the text the user actually saw is a prefix of
     what was produced.
  2. Completed but unsettled — the last token was delivered and the
     settlement commit then failed.
  3. Cancelled server-side — a deadline or a spend ceiling terminated the
     stream partway.

`stream_state` distinguishes an assistant row that is still being written
from one that reached a terminal state; `finish_reason` records *which*
terminal state; `truncated` records that the persisted text is a prefix of
what the model produced. `usage_estimated` is the metering counterpart: it
marks a row whose token counts came from a local emission count rather than
the provider's usage metadata, because the stream ended before that metadata
arrived. An estimate you have flagged is reconcilable in ARCH-14; a missing
row is not.

WHY `stream_state` IS AN ENUM AND NOT A BOOLEAN
-----------------------------------------------
ARCH-14 reconciliation reads this column to decide which rows to compare
against provider invoices. A boolean `is_complete` collapses "still
streaming" and "died before terminal state" into the same value, and those
two need different treatment: the first is expected and transient, the second
is an incident. Following the ARCH-07 §B.1 precedent, the type is created
here with `create_type=True` because it is new in this revision and nothing
else references it.

WHY THE PARTIAL INDEX
---------------------
`ix_conversation_messages_in_flight` serves exactly one query — the sweeper
that finds rows left in STREAMING past a deadline, which is how state (2)
above is detected at all. It is partial because in a healthy system the
matching set is empty, and an index over an empty predicate costs nothing.

EXPAND-ONLY. Every column is nullable or carries a server default, so this
revision rejects no write that succeeds today. The `finish_reason` CHECK is
the one constraint that is safe to ship immediately: nothing writes the
column before this migration, so the vocabulary cannot already be violated.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch12_step1_stream_state"
down_revision = "arch11_5_intent_config"
branch_labels = None
depends_on = None

STREAM_STATE_ENUM = "conversation_stream_state"
STREAM_STATES: tuple[str, ...] = ("NONE", "STREAMING", "COMPLETE", "ABORTED")

FINISH_REASONS: tuple[str, ...] = (
    "completed",
    "client_disconnected",
    "provider_error",
    "deadline_exceeded",
    "spend_limit",
    "output_ceiling",
    "filtered",
)


def upgrade() -> None:
    stream_state = postgresql.ENUM(
        *STREAM_STATES,
        name=STREAM_STATE_ENUM,
        create_type=False,
    )
    stream_state.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "conversation_messages",
        sa.Column(
            "stream_state",
            stream_state,
            nullable=False,
            server_default=sa.text(f"'NONE'::{STREAM_STATE_ENUM}"),
            comment=(
                "ARCH-12 Step 1. NONE for every row written by the synchronous "
                "path. STREAMING is written before the first token and is a "
                "non-terminal state; COMPLETE and ABORTED are terminal."
            ),
        ),
    )

    op.add_column(
        "conversation_messages",
        sa.Column(
            "truncated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment=(
                "ARCH-12 Step 1. The persisted content is a prefix of what the "
                "provider generated. A user who scrolls back must see what "
                "they saw, and must be able to tell that it was cut short."
            ),
        ),
    )

    op.add_column(
        "conversation_messages",
        sa.Column(
            "finish_reason",
            sa.String(40),
            nullable=True,
            comment="ARCH-12 Step 1. Terminal cause; NULL while STREAMING.",
        ),
    )

    op.add_column(
        "conversation_messages",
        sa.Column(
            "usage_estimated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment=(
                "ARCH-12 Step 1. Token counts on this row came from a local "
                "emission count, not provider usage metadata. Read by ARCH-14 "
                "provider reconciliation (A8)."
            ),
        ),
    )

    op.add_column(
        "conversation_messages",
        sa.Column(
            "stream_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "ARCH-12 Step 1. Set when the row enters STREAMING. The "
                "in-flight sweeper measures its deadline from here, not from "
                "created_at, because a retried generation reuses the row."
            ),
        ),
    )

    op.create_check_constraint(
        "ck_conversation_messages_finish_reason",
        "conversation_messages",
        "finish_reason IS NULL OR finish_reason IN ("
        + ", ".join(f"'{value}'" for value in FINISH_REASONS)
        + ")",
    )

    # A terminal state must name its cause. STREAMING must not.
    op.create_check_constraint(
        "ck_conversation_messages_stream_state_reason",
        "conversation_messages",
        "(stream_state IN ('NONE', 'STREAMING') AND finish_reason IS NULL) "
        "OR (stream_state IN ('COMPLETE', 'ABORTED') AND finish_reason IS NOT NULL)",
    )

    op.create_index(
        "ix_conversation_messages_in_flight",
        "conversation_messages",
        ["stream_started_at"],
        postgresql_where=sa.text(
            f"stream_state = 'STREAMING'::{STREAM_STATE_ENUM}"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_messages_in_flight", table_name="conversation_messages"
    )
    op.drop_constraint(
        "ck_conversation_messages_stream_state_reason",
        "conversation_messages",
        type_="check",
    )
    op.drop_constraint(
        "ck_conversation_messages_finish_reason",
        "conversation_messages",
        type_="check",
    )
    op.drop_column("conversation_messages", "stream_started_at")
    op.drop_column("conversation_messages", "usage_estimated")
    op.drop_column("conversation_messages", "finish_reason")
    op.drop_column("conversation_messages", "truncated")
    op.drop_column("conversation_messages", "stream_state")

    postgresql.ENUM(name=STREAM_STATE_ENUM).drop(op.get_bind(), checkfirst=True)
