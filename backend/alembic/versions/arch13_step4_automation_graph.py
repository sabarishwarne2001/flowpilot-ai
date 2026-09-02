"""ARCH-13 Step 13.4 — automation_nodes, automation_edges, graph_version

Revision ID: arch13_step4_automation_graph
Revises: arch13_step3_automation_executions
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch13_step4_automation_graph"
down_revision = "arch13_step3_automation_executions"
branch_labels = None
depends_on = None


NODE_TYPES = ("trigger", "condition", "action", "branch", "join")
BRANCH_LABELS = ("default", "true", "false")


def upgrade() -> None:
    op.add_column(
        "automation_rules",
        sa.Column(
            "graph_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "graph_version_known",
        "automation_rules",
        "graph_version IN (0, 1)",
    )

    op.add_column(
        "automation_rules",
        sa.Column(
            "on_error",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'HALT'"),
        ),
    )
    op.create_check_constraint(
        "on_error_known",
        "automation_rules",
        "on_error IN ('HALT', 'CONTINUE')",
    )

    op.add_column(
        "automation_rules",
        sa.Column("budget_cost_micros", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "budget_non_negative",
        "automation_rules",
        "budget_cost_micros IS NULL OR budget_cost_micros >= 0",
    )

    op.create_table(
        "automation_nodes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_key", sa.String(length=64), nullable=False),
        sa.Column("node_type", sa.String(length=32), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("position", postgresql.JSONB(), nullable=True),
        sa.Column("topological_order", sa.Integer(), nullable=False),
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
            "node_type IN ('trigger', 'condition', 'action', 'branch', 'join')",
            name="ck_automation_nodes_type_known",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(config) = 'object'",
            name="ck_automation_nodes_config_is_object",
        ),
        sa.CheckConstraint(
            "topological_order >= 0",
            name="ck_automation_nodes_order_non_negative",
        ),
        sa.CheckConstraint(
            "node_key <> '' AND node_key !~ '\\s'",
            name="ck_automation_nodes_key_shape",
        ),
        sa.UniqueConstraint(
            "rule_id", "node_key", name="uq_automation_nodes_rule_node_key"
        ),
        sa.UniqueConstraint(
            "rule_id",
            "topological_order",
            name="uq_automation_nodes_rule_order",
        ),
    )

    op.create_index(
        "ix_automation_nodes_rule_order",
        "automation_nodes",
        ["rule_id", "topological_order"],
    )

    op.create_table(
        "automation_edges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_node_key", sa.String(length=64), nullable=False),
        sa.Column("to_node_key", sa.String(length=64), nullable=False),
        sa.Column(
            "branch",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'default'"),
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
            "branch IN ('default', 'true', 'false')",
            name="ck_automation_edges_branch_known",
        ),
        sa.CheckConstraint(
            "from_node_key <> to_node_key",
            name="ck_automation_edges_no_self_loop",
        ),
        sa.UniqueConstraint(
            "rule_id",
            "from_node_key",
            "to_node_key",
            "branch",
            name="uq_automation_edges_rule_from_to_branch",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id", "from_node_key"],
            ["automation_nodes.rule_id", "automation_nodes.node_key"],
            name="fk_automation_edges_from_node",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id", "to_node_key"],
            ["automation_nodes.rule_id", "automation_nodes.node_key"],
            name="fk_automation_edges_to_node",
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "ix_automation_edges_rule_from",
        "automation_edges",
        ["rule_id", "from_node_key"],
    )

    op.alter_column("automation_rules", "graph_version", server_default=None)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS automation_edges CASCADE;")
    op.execute("DROP TABLE IF EXISTS automation_nodes CASCADE;")

    op.execute(
        """
        DO $$
        DECLARE
            r RECORD;
        BEGIN
            FOR r IN (
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'automation_rules'::regclass
                  AND conname IN (
                      'ck_automation_rules_budget_non_negative',
                      'ck_automation_rules_on_error_known',
                      'ck_automation_rules_graph_version_known',
                      'budget_non_negative',
                      'on_error_known',
                      'graph_version_known'
                  )
            ) LOOP
                EXECUTE 'ALTER TABLE automation_rules DROP CONSTRAINT IF EXISTS ' || quote_ident(r.conname);
            END LOOP;
        END $$;
        """
    )

    op.execute(
        """
        ALTER TABLE automation_rules
          DROP COLUMN IF EXISTS budget_cost_micros,
          DROP COLUMN IF EXISTS on_error,
          DROP COLUMN IF EXISTS graph_version;
        """
    )
