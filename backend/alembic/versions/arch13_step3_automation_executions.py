"""ARCH-13 Step 13.3 — automation_executions, automation_node_runs (A6)

Revision ID: arch13_step3_automation_executions
Revises: arch13_step2_outbox_causality
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch13_step3_automation_executions"
down_revision = "arch13_step2_outbox_causality"
branch_labels = None
depends_on = None


EXECUTION_STATUS_ENUM = "automation_execution_status"
NODE_RUN_STATUS_ENUM = "automation_node_run_status"

EXECUTION_STATUSES = (
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "TIMED_OUT",
    "SUPPRESSED_CYCLE",
    "SUPPRESSED_DEPTH",
    "BUDGET_EXHAUSTED",
)

NODE_RUN_STATUSES = (
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "SKIPPED",
    "TIMED_OUT",
    "BUDGET_EXHAUSTED",
)

TERMINAL_STATUS_SQL = (
    "'COMPLETED', 'FAILED', 'TIMED_OUT', 'SUPPRESSED_CYCLE', "
    "'SUPPRESSED_DEPTH', 'BUDGET_EXHAUSTED'"
)


def upgrade() -> None:
    execution_status = postgresql.ENUM(
        *EXECUTION_STATUSES, name=EXECUTION_STATUS_ENUM, create_type=False
    )
    node_run_status = postgresql.ENUM(
        *NODE_RUN_STATUSES, name=NODE_RUN_STATUS_ENUM, create_type=False
    )
    execution_status.create(op.get_bind(), checkfirst=True)
    node_run_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "automation_executions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation_rules.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "outbox_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("outbox_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", execution_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("budget_cost_micros", sa.BigInteger(), nullable=False),
        sa.Column(
            "spent_cost_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "node_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "nodes_executed", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "actions_executed",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "emitted_event_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "spent_cost_micros <= budget_cost_micros",
            name="ck_automation_executions_spend_within_budget",
        ),
        sa.CheckConstraint(
            "budget_cost_micros >= 0 AND spent_cost_micros >= 0",
            name="ck_automation_executions_costs_non_negative",
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING'::automation_execution_status) = "
            "(deadline_at IS NOT NULL)",
            name="ck_automation_executions_deadline_matches_status",
        ),
        sa.CheckConstraint(
            f"(status IN ({TERMINAL_STATUS_SQL})) = (completed_at IS NOT NULL)",
            name="ck_automation_executions_completed_at_matches_status",
        ),
        sa.CheckConstraint(
            "depth >= 0 AND depth <= 16",
            name="ck_automation_executions_depth_bounded",
        ),
        sa.CheckConstraint(
            "nodes_executed >= 0 AND nodes_executed <= node_count",
            name="ck_automation_executions_nodes_executed_bounded",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(emitted_event_ids) = 'array'",
            name="ck_automation_executions_emitted_is_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'",
            name="ck_automation_executions_details_is_object",
        ),
    )

    op.create_index(
        "uq_automation_executions_rule_event",
        "automation_executions",
        ["rule_id", "outbox_event_id"],
        unique=True,
        postgresql_where=sa.text("outbox_event_id IS NOT NULL"),
    )

    op.create_index(
        "ix_automation_executions_correlation_rule",
        "automation_executions",
        ["correlation_id", "rule_id"],
    )

    op.create_index(
        "ix_automation_executions_running_deadline",
        "automation_executions",
        ["deadline_at"],
        postgresql_where=sa.text(
            "status = 'RUNNING'::automation_execution_status"
        ),
    )

    op.create_index(
        "ix_automation_executions_workspace_created",
        "automation_executions",
        ["workspace_id", sa.text("created_at DESC")],
    )

    op.create_index(
        "ix_automation_executions_emitted_event_ids",
        "automation_executions",
        ["emitted_event_ids"],
        postgresql_using="gin",
    )

    op.create_table(
        "automation_node_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation_executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_key", sa.String(length=64), nullable=False),
        sa.Column("node_type", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", node_run_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_digest", sa.String(length=71), nullable=True),
        sa.Column("output_digest", sa.String(length=71), nullable=True),
        sa.Column(
            "cost_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "cost_micros >= 0", name="ck_automation_node_runs_cost_non_negative"
        ),
        sa.CheckConstraint(
            "sequence >= 0", name="ck_automation_node_runs_sequence_non_negative"
        ),
        sa.CheckConstraint(
            "input_digest IS NULL OR input_digest LIKE 'sha256:%'",
            name="ck_automation_node_runs_input_digest_prefixed",
        ),
        sa.CheckConstraint(
            "output_digest IS NULL OR output_digest LIKE 'sha256:%'",
            name="ck_automation_node_runs_output_digest_prefixed",
        ),
        sa.UniqueConstraint(
            "execution_id", "sequence", name="uq_automation_node_runs_execution_sequence"
        ),
    )

    op.create_index(
        "ix_automation_node_runs_execution_sequence",
        "automation_node_runs",
        ["execution_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_automation_node_runs_execution_sequence", table_name="automation_node_runs"
    )
    op.drop_table("automation_node_runs")

    op.drop_index(
        "ix_automation_executions_emitted_event_ids",
        table_name="automation_executions",
    )
    op.drop_index(
        "ix_automation_executions_workspace_created",
        table_name="automation_executions",
    )
    op.drop_index(
        "ix_automation_executions_running_deadline",
        table_name="automation_executions",
    )
    op.drop_index(
        "ix_automation_executions_correlation_rule",
        table_name="automation_executions",
    )
    op.drop_index(
        "uq_automation_executions_rule_event", table_name="automation_executions"
    )
    op.drop_table("automation_executions")

    postgresql.ENUM(name=NODE_RUN_STATUS_ENUM).drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name=EXECUTION_STATUS_ENUM).drop(op.get_bind(), checkfirst=True)
