"""Phase 2 — TruthMesh, the cross-document digital twin.

Revision ID: p9a1_truthmesh_engine
Revises: p8a2_batch_dispatch_engine
Create Date: 2026-10-08

Five new tables and one audit resource type; nothing existing changed (additive; downgrade drops
the tables):

  mesh_nodes        one per document: kind, rank, typed facts, terms, identifiers, parties, the
                    semantic centroid (vector(384), the mean of the document's chunk embeddings)
  mesh_links        typed, bidirectional links between two documents with their signals
  mesh_conflicts    cross-document discrepancies, kept across rebuilds by fingerprint
  mesh_simulations  saved what-if runs and the ripple they produced
  mesh_states       one per workspace: last build, size, open conflicts, risk index

The centroid column is added by raw DDL (as arch34 added document_fingerprints.embedding): the
migration does not import pgvector's SQLAlchemy type. There is deliberately no HNSW index on it:
a filtered HNSW scan over a shared index starves small tenants (see app/models/document_chunk.py);
a workspace's nodes are read by the workspace index and compared exactly.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "p9a1_truthmesh_engine"
down_revision = "p8a2_batch_dispatch_engine"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
NOW = sa.text("now()")
EMBEDDING_DIMENSION = 384


def _ts(name: str, nullable: bool = False) -> sa.Column:
    if nullable:
        return sa.Column(name, sa.DateTime(timezone=True), nullable=True)
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=NOW)


def _work_item_fk(column: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column, "workspace_id"], ["work_items.id", "work_items.workspace_id"], name=name, ondelete="CASCADE",
    )


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id"], ["organizations.id"],
        name=f"fk_{table}_organization_id_organizations", ondelete="CASCADE",
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS 'TRUTH_MESH'")

    op.create_table(
        "mesh_nodes",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("work_item_id", UUID, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("kind_label", sa.String(80), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("document_number", sa.String(128), nullable=True),
        sa.Column("counterparty", sa.String(300), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("amount_micros", sa.BigInteger(), nullable=True),
        sa.Column("net_amount_micros", sa.BigInteger(), nullable=True),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("facts", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("terms", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("identifiers", postgresql.ARRAY(sa.String(128)), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("referenced_identifiers", postgresql.ARRAY(sa.String(128)), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("parties", postgresql.ARRAY(sa.String(300)), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("risk_score", sa.Numeric(5, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("degree", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        _ts("indexed_at"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_mesh_nodes"),
        sa.UniqueConstraint("work_item_id", name="uq_mesh_nodes_work_item"),
        sa.CheckConstraint("rank >= 0 AND rank <= 9", name="ck_mesh_nodes_rank"),
        sa.CheckConstraint("risk_score >= 0 AND risk_score <= 100", name="ck_mesh_nodes_risk_range"),
        sa.CheckConstraint("jsonb_typeof(facts) = 'array'", name="ck_mesh_nodes_facts_array"),
        sa.CheckConstraint("jsonb_typeof(terms) = 'object'", name="ck_mesh_nodes_terms_object"),
        sa.CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="ck_mesh_nodes_currency_shape"),
        _work_item_fk("work_item_id", "fk_mesh_nodes_work_item_workspace"),
        _org_fk("mesh_nodes"),
    )
    op.execute(f"ALTER TABLE mesh_nodes ADD COLUMN centroid vector({EMBEDDING_DIMENSION}) NULL")
    op.create_index("ix_mesh_nodes_workspace_kind", "mesh_nodes", ["workspace_id", "kind"])
    op.create_index("ix_mesh_nodes_workspace_risk", "mesh_nodes", ["workspace_id", "risk_score"])

    op.create_table(
        "mesh_links",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("source_work_item_id", UUID, nullable=False),
        sa.Column("target_work_item_id", UUID, nullable=False),
        sa.Column("relation", sa.String(24), nullable=False),
        sa.Column("directed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("strength", sa.Numeric(5, 4), nullable=False),
        sa.Column("method", sa.String(12), nullable=False),
        sa.Column("signals", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(10), nullable=False, server_default=sa.text("'AUTO'")),
        sa.Column("decided_by_user_id", UUID, nullable=True),
        _ts("decided_at", nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_mesh_links"),
        sa.UniqueConstraint("workspace_id", "source_work_item_id", "target_work_item_id", name="uq_mesh_links_pair"),
        sa.CheckConstraint("source_work_item_id <> target_work_item_id", name="ck_mesh_links_not_self"),
        sa.CheckConstraint("strength >= 0 AND strength <= 1", name="ck_mesh_links_strength_ratio"),
        sa.CheckConstraint("status IN ('AUTO', 'CONFIRMED', 'REJECTED')", name="ck_mesh_links_status"),
        sa.CheckConstraint("jsonb_typeof(signals) = 'array'", name="ck_mesh_links_signals_array"),
        _work_item_fk("source_work_item_id", "fk_mesh_links_source_workspace"),
        _work_item_fk("target_work_item_id", "fk_mesh_links_target_workspace"),
        _org_fk("mesh_links"),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"], ["users.id"], name="fk_mesh_links_decided_by_user_id_users", ondelete="SET NULL",
        ),
    )
    op.create_index("ix_mesh_links_workspace_source", "mesh_links", ["workspace_id", "source_work_item_id"])
    op.create_index("ix_mesh_links_workspace_target", "mesh_links", ["workspace_id", "target_work_item_id"])

    op.create_table(
        "mesh_conflicts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("concept", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default=sa.text("'OPEN'")),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("relation", sa.String(24), nullable=True),
        sa.Column("work_item_ids", postgresql.ARRAY(UUID), nullable=False),
        sa.Column("document_values", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("details", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("exposure_micros", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("auto_resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by_user_id", UUID, nullable=True),
        _ts("resolved_at", nullable=True),
        _ts("first_seen_at"),
        _ts("last_seen_at"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_mesh_conflicts"),
        sa.UniqueConstraint("workspace_id", "fingerprint", name="uq_mesh_conflicts_fingerprint"),
        sa.CheckConstraint("status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'DISMISSED')",
                           name="ck_mesh_conflicts_status"),
        sa.CheckConstraint("severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')", name="ck_mesh_conflicts_severity"),
        sa.CheckConstraint("cardinality(work_item_ids) >= 1", name="ck_mesh_conflicts_documents"),
        sa.CheckConstraint("jsonb_typeof(document_values) = 'array'", name="ck_mesh_conflicts_values_array"),
        sa.CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_mesh_conflicts_details_object"),
        sa.CheckConstraint("exposure_micros IS NULL OR exposure_micros >= 0", name="ck_mesh_conflicts_exposure"),
        sa.CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="ck_mesh_conflicts_currency_shape"),
        sa.CheckConstraint("status NOT IN ('RESOLVED', 'DISMISSED') OR resolved_at IS NOT NULL",
                           name="ck_mesh_conflicts_resolution_recorded"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_mesh_conflicts_workspace_id_workspaces", ondelete="CASCADE",
        ),
        _org_fk("mesh_conflicts"),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"], ["users.id"], name="fk_mesh_conflicts_resolved_by_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_mesh_conflicts_workspace_status", "mesh_conflicts", ["workspace_id", "status", "severity"])
    op.create_index("ix_mesh_conflicts_documents", "mesh_conflicts", ["work_item_ids"], postgresql_using="gin")

    op.create_table(
        "mesh_simulations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("origin_work_item_id", UUID, nullable=False),
        sa.Column("created_by_user_id", UUID, nullable=True),
        sa.Column("scenario", sa.String(24), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("parameters", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("documents_affected", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("exposure_micros", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_mesh_simulations"),
        sa.CheckConstraint("jsonb_typeof(parameters) = 'object'", name="ck_mesh_simulations_parameters_object"),
        sa.CheckConstraint("jsonb_typeof(result) = 'object'", name="ck_mesh_simulations_result_object"),
        _work_item_fk("origin_work_item_id", "fk_mesh_simulations_origin_workspace"),
        _org_fk("mesh_simulations"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name="fk_mesh_simulations_created_by_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_mesh_simulations_workspace_created", "mesh_simulations", ["workspace_id", "created_at"])

    op.create_table(
        "mesh_states",
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default=sa.text("'EMPTY'")),
        sa.Column("nodes", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("links", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("open_conflicts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("risk_index", sa.Numeric(5, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("build_ms", sa.Integer(), nullable=True),
        sa.Column("engine_version", sa.String(24), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _ts("last_built_at", nullable=True),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("workspace_id", name="pk_mesh_states"),
        sa.CheckConstraint("status IN ('EMPTY', 'BUILDING', 'READY', 'FAILED')", name="ck_mesh_states_status"),
        sa.CheckConstraint("risk_index >= 0 AND risk_index <= 100", name="ck_mesh_states_risk_range"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_mesh_states_workspace_id_workspaces", ondelete="CASCADE",
        ),
        _org_fk("mesh_states"),
    )


def downgrade() -> None:
    # The audit resource type stays: PostgreSQL cannot drop an enum value, and audit rows may use it.
    op.drop_table("mesh_states")
    op.drop_table("mesh_simulations")
    op.drop_table("mesh_conflicts")
    op.drop_table("mesh_links")
    op.drop_table("mesh_nodes")
