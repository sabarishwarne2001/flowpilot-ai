"""ARCH-13 Step 13.2 — outbox_events causal chain (EXPAND, A7)

Three columns, not one. A depth bound *truncates* a cycle; it does not detect
one. Two rules ping-ponging under MAX_DEPTH=5 run five times on every document,
cost five LLM calls, and produce no error — the operator sees elevated spend
and nothing else.

  depth           hops from the originating event; the backstop for a chain of
                  distinct rules, which cycle detection cannot see.
  causation_id    the event that caused this one. One hop back.
  correlation_id  the root of the chain. Constant along it, so "everything this
                  upload caused" is one indexed query — and so cycle detection
                  has a set to look for a repeated rule_id in.

Revision ID: arch13_step2_outbox_causality
Revises: arch13_step1_outbox_internal_events
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch13_step2_outbox_causality"
down_revision = "arch13_step1_outbox_internal_events"
branch_labels = None
depends_on = None


#: The database ceiling. Deliberately far above AUTOMATION_MAX_DEPTH (5) so the
#: application refuses first, with a message naming the rules involved, and
#: this constraint only ever fires on a bug in that refusal.
HARD_DEPTH_CEILING = 16


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column(
            "depth", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "outbox_events",
        sa.Column("causation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # ON DELETE SET NULL, not CASCADE: pruning a published root event must not
    # delete the audit trail of everything it caused. ARCH-09's
    # `ix_outbox_events_prunable` exists precisely to prune published rows.
    op.create_foreign_key(
        "fk_outbox_events_causation_id_outbox_events",
        "outbox_events",
        "outbox_events",
        ["causation_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_check_constraint(
        "depth_bounded",
        "outbox_events",
        f"depth >= 0 AND depth <= {HARD_DEPTH_CEILING}",
    )

    # A caused event with no correlation root is a chain whose trace is
    # unrecoverable — cycle detection cannot run on it and "what did this
    # upload cause" cannot answer. Refuse it at write time.
    op.create_check_constraint(
        "causation_implies_correlation",
        "outbox_events",
        "causation_id IS NULL OR correlation_id IS NOT NULL",
    )

    # A root event (depth 0) is its own correlation root or has none. A
    # non-root event must have one. This is the constraint that catches an
    # emit path that increments depth but forgets to thread correlation.
    op.create_check_constraint(
        "depth_implies_correlation",
        "outbox_events",
        "depth = 0 OR correlation_id IS NOT NULL",
    )

    op.create_index(
        "ix_outbox_events_correlation",
        "outbox_events",
        ["correlation_id", "seq"],
        postgresql_where=sa.text("correlation_id IS NOT NULL"),
    )
    op.create_index(
        "ix_outbox_events_causation",
        "outbox_events",
        ["causation_id"],
        postgresql_where=sa.text("causation_id IS NOT NULL"),
    )

    op.alter_column("outbox_events", "depth", server_default=None)


def downgrade() -> None:
    op.alter_column("outbox_events", "depth", server_default=sa.text("0"))
    op.drop_index("ix_outbox_events_causation", table_name="outbox_events")
    op.drop_index("ix_outbox_events_correlation", table_name="outbox_events")
    op.drop_constraint(
        "ck_outbox_events_depth_implies_correlation", "outbox_events", type_="check"
    )
    op.drop_constraint(
        "ck_outbox_events_causation_implies_correlation",
        "outbox_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_outbox_events_depth_bounded", "outbox_events", type_="check"
    )
    op.drop_constraint(
        "fk_outbox_events_causation_id_outbox_events",
        "outbox_events",
        type_="foreignkey",
    )
    op.drop_column("outbox_events", "correlation_id")
    op.drop_column("outbox_events", "causation_id")
    op.drop_column("outbox_events", "depth")
