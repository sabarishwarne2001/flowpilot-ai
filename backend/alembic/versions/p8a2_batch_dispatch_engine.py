"""Phase 1 — the batch processing & document dispatch engine.

Revision ID: p8a2_batch_dispatch_engine
Revises: p8a1_work_item_duplicates
Create Date: 2026-10-08

Five new tables and two audit resource types; nothing existing changed (additive; downgrade drops
the tables):

  processing_batches       a named set of a workspace's documents (from a selection or an upload)
  processing_batch_items   batch membership, and the lane each document was last dispatched to
  dispatch_policies        a workspace's confidence thresholds for the three lanes
  export_packages          zips of a batch's data with a SHA-256 manifest, built by the worker
  schema_healing_events    fields renamed and retyped to their schema, with the original kept

Composite foreign keys onto work_items (id, workspace_id) keep a batch from ever holding another
workspace's document; the models in app/models/batches.py declare every constraint below.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "p8a2_batch_dispatch_engine"
down_revision = "p8a1_work_item_duplicates"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
NOW = sa.text("now()")


def _ts(name: str, nullable: bool = False) -> sa.Column:
    if nullable:
        return sa.Column(name, sa.DateTime(timezone=True), nullable=True)
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=NOW)


def upgrade() -> None:
    # Two audit resource types (app/models/audit_log.py). ADD VALUE cannot run inside the migration's
    # transaction on every server version; the autocommit block is how p6a1 added WORK_ITEM.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS 'PROCESSING_BATCH'")
        op.execute("ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS 'EXPORT_PACKAGE'")

    op.create_table(
        "processing_batches",
        sa.Column("id", UUID, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("ingestion_batch_id", UUID, nullable=True),
        sa.Column("created_by_user_id", UUID, nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _ts("dispatched_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_processing_batches"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_processing_batches_id_workspace_id"),
        sa.CheckConstraint("source IN ('SELECTION', 'INGESTION')", name="ck_processing_batches_source"),
        sa.CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="ck_processing_batches_status"),
        sa.CheckConstraint("char_length(btrim(name)) > 0", name="ck_processing_batches_name_not_blank"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"],
            name="fk_processing_batches_workspace_id_workspaces", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_processing_batches_organization_id_organizations", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"],
            name="fk_processing_batches_ingestion_batch_id_ingestion_batches", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name="fk_processing_batches_created_by_user_id_users", ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_processing_batches_workspace_created", "processing_batches", ["workspace_id", "created_at"]
    )

    op.create_table(
        "processing_batch_items",
        sa.Column("batch_id", UUID, nullable=False),
        sa.Column("work_item_id", UUID, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        _ts("added_at"),
        sa.Column("lane", sa.String(24), nullable=True),
        sa.Column("lane_reasons", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        _ts("dispatched_at", nullable=True),
        sa.PrimaryKeyConstraint("batch_id", "work_item_id", name="pk_processing_batch_items"),
        sa.CheckConstraint(
            "lane IS NULL OR lane IN ('STRAIGHT_THROUGH', 'REVIEW', 'EXCEPTION')",
            name="ck_processing_batch_items_lane",
        ),
        sa.CheckConstraint(
            "(lane IS NULL) = (dispatched_at IS NULL)",
            name="ck_processing_batch_items_lane_matches_dispatch",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_processing_batch_items_confidence_ratio",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(lane_reasons) = 'array'", name="ck_processing_batch_items_reasons_array"
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "workspace_id"],
            ["processing_batches.id", "processing_batches.workspace_id"],
            name="fk_processing_batch_items_batch_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id", "workspace_id"],
            ["work_items.id", "work_items.workspace_id"],
            name="fk_processing_batch_items_work_item_workspace",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_processing_batch_items_work_item", "processing_batch_items", ["work_item_id"])
    op.create_index("ix_processing_batch_items_batch_lane", "processing_batch_items", ["batch_id", "lane"])

    op.create_table(
        "dispatch_policies",
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column(
            "straight_through_min_confidence", sa.Numeric(5, 4), nullable=False,
            server_default=sa.text("0.9000"),
        ),
        sa.Column("review_min_confidence", sa.Numeric(5, 4), nullable=False, server_default=sa.text("0.6000")),
        sa.Column("require_required_fields", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("tag_documents", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_by_user_id", UUID, nullable=True),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("workspace_id", name="pk_dispatch_policies"),
        sa.CheckConstraint(
            "straight_through_min_confidence >= 0 AND straight_through_min_confidence <= 1",
            name="ck_dispatch_policies_straight_through_ratio",
        ),
        sa.CheckConstraint(
            "review_min_confidence >= 0 AND review_min_confidence <= 1",
            name="ck_dispatch_policies_review_ratio",
        ),
        sa.CheckConstraint(
            "review_min_confidence <= straight_through_min_confidence",
            name="ck_dispatch_policies_review_below_straight_through",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"],
            name="fk_dispatch_policies_workspace_id_workspaces", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"],
            name="fk_dispatch_policies_updated_by_user_id_users", ondelete="SET NULL",
        ),
    )

    op.create_table(
        "export_packages",
        sa.Column("id", UUID, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("batch_id", UUID, nullable=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'QUEUED'")),
        sa.Column("include_originals", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("work_item_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("document_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("package_sha256", sa.String(64), nullable=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=True),
        sa.Column("manifest", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("storage_key", sa.String(512), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("download_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_by_user_id", UUID, nullable=True),
        _ts("created_at"),
        _ts("completed_at", nullable=True),
        _ts("expires_at", nullable=True),
        _ts("last_downloaded_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_export_packages"),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'BUILDING', 'READY', 'FAILED', 'EXPIRED')", name="ck_export_packages_status"
        ),
        sa.CheckConstraint(
            "(status = 'READY') = (package_sha256 IS NOT NULL AND storage_key IS NOT NULL)",
            name="ck_export_packages_ready_has_digest",
        ),
        sa.CheckConstraint(
            "(status = 'FAILED') = (error_code IS NOT NULL)", name="ck_export_packages_failed_has_code"
        ),
        sa.CheckConstraint(
            "package_sha256 IS NULL OR package_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_export_packages_package_sha256_hex",
        ),
        sa.CheckConstraint(
            "manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_export_packages_manifest_sha256_hex",
        ),
        sa.CheckConstraint("jsonb_typeof(manifest) = 'array'", name="ck_export_packages_manifest_array"),
        sa.CheckConstraint("jsonb_typeof(work_item_ids) = 'array'", name="ck_export_packages_ids_array"),
        sa.CheckConstraint("char_length(btrim(name)) > 0", name="ck_export_packages_name_not_blank"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"],
            name="fk_export_packages_workspace_id_workspaces", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_export_packages_organization_id_organizations", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["processing_batches.id"],
            name="fk_export_packages_batch_id_processing_batches", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name="fk_export_packages_created_by_user_id_users", ondelete="SET NULL",
        ),
    )
    op.create_index("ix_export_packages_workspace_created", "export_packages", ["workspace_id", "created_at"])
    op.create_index("ix_export_packages_batch", "export_packages", ["batch_id"])

    op.create_table(
        "schema_healing_events",
        sa.Column("id", UUID, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("work_item_id", UUID, nullable=False),
        sa.Column("batch_id", UUID, nullable=True),
        sa.Column("schema_key", sa.String(96), nullable=False),
        sa.Column("changes", JSONB, nullable=False),
        sa.Column("original_entities", JSONB, nullable=False),
        sa.Column("result_sha256", sa.String(64), nullable=False),
        sa.Column("applied_by_user_id", UUID, nullable=True),
        _ts("applied_at"),
        _ts("reverted_at", nullable=True),
        sa.Column("reverted_by_user_id", UUID, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_schema_healing_events"),
        sa.CheckConstraint("jsonb_typeof(changes) = 'array'", name="ck_schema_healing_events_changes_array"),
        sa.CheckConstraint(
            "reverted_by_user_id IS NULL OR reverted_at IS NOT NULL",
            name="ck_schema_healing_events_revert_pair",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id", "workspace_id"],
            ["work_items.id", "work_items.workspace_id"],
            name="fk_schema_healing_events_work_item_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["processing_batches.id"],
            name="fk_schema_healing_events_batch_id_processing_batches", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["applied_by_user_id"], ["users.id"],
            name="fk_schema_healing_events_applied_by_user_id_users", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reverted_by_user_id"], ["users.id"],
            name="fk_schema_healing_events_reverted_by_user_id_users", ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_schema_healing_events_work_item", "schema_healing_events", ["work_item_id", "applied_at"]
    )
    op.create_index("ix_schema_healing_events_batch", "schema_healing_events", ["batch_id"])


def downgrade() -> None:
    # The two audit resource types stay: PostgreSQL cannot drop an enum value, and audit rows may
    # use them. Unused values are harmless.
    op.drop_table("schema_healing_events")
    op.drop_table("export_packages")
    op.drop_table("dispatch_policies")
    op.drop_table("processing_batch_items")
    op.drop_table("processing_batches")
