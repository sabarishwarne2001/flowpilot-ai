"""Phase 1 — the batch processing & document dispatch engine (capability.batch_dispatch).

A processing batch is a named set of a workspace's documents, followed from
upload to hand-off: how far processing has got, how confident the extraction
is, which fields drifted from their schema, and which lane each document is
dispatched to (straight through, human review, or exception). Export packages
bundle a batch's data (and optionally its original files) into a zip whose
every file is listed with its SHA-256 in a manifest, so a compliance team can
prove later that what they hold is exactly what was exported.

The schema lives in alembic/versions/p8a2_batch_dispatch_engine.py; these
mappings declare every constraint and index so the drift gate sees none.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: Where a dispatched document goes next.
LANE_STRAIGHT_THROUGH = "STRAIGHT_THROUGH"
LANE_REVIEW = "REVIEW"
LANE_EXCEPTION = "EXCEPTION"
LANES: tuple[str, ...] = (LANE_STRAIGHT_THROUGH, LANE_REVIEW, LANE_EXCEPTION)

BATCH_SOURCES: tuple[str, ...] = ("SELECTION", "INGESTION")
BATCH_STATUSES: tuple[str, ...] = ("ACTIVE", "ARCHIVED")

PACKAGE_QUEUED = "QUEUED"
PACKAGE_BUILDING = "BUILDING"
PACKAGE_READY = "READY"
PACKAGE_FAILED = "FAILED"
PACKAGE_EXPIRED = "EXPIRED"
PACKAGE_STATUSES: tuple[str, ...] = (
    PACKAGE_QUEUED,
    PACKAGE_BUILDING,
    PACKAGE_READY,
    PACKAGE_FAILED,
    PACKAGE_EXPIRED,
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ProcessingBatch(Base):
    """A named set of documents, followed from upload to dispatch."""

    __tablename__ = "processing_batches"

    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_processing_batches_id_workspace_id"),
        CheckConstraint(_in("source", BATCH_SOURCES), name="ck_processing_batches_source"),
        CheckConstraint(_in("status", BATCH_STATUSES), name="ck_processing_batches_status"),
        CheckConstraint("char_length(btrim(name)) > 0", name="ck_processing_batches_name_not_blank"),
        Index("ix_processing_batches_workspace_created", "workspace_id", "created_at"),
        ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"],
            name="fk_processing_batches_workspace_id_workspaces", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_processing_batches_organization_id_organizations", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"],
            name="fk_processing_batches_ingestion_batch_id_ingestion_batches", ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name="fk_processing_batches_created_by_user_id_users", ondelete="SET NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ACTIVE'"))
    ingestion_batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessingBatchItem(Base):
    """One document in a batch, with the lane it was last dispatched to."""

    __tablename__ = "processing_batch_items"

    __table_args__ = (
        PrimaryKeyConstraint("batch_id", "work_item_id", name="pk_processing_batch_items"),
        CheckConstraint(
            "lane IS NULL OR " + _in("lane", LANES), name="ck_processing_batch_items_lane"
        ),
        CheckConstraint(
            "(lane IS NULL) = (dispatched_at IS NULL)",
            name="ck_processing_batch_items_lane_matches_dispatch",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_processing_batch_items_confidence_ratio",
        ),
        CheckConstraint(
            "jsonb_typeof(lane_reasons) = 'array'", name="ck_processing_batch_items_reasons_array"
        ),
        Index("ix_processing_batch_items_work_item", "work_item_id"),
        Index("ix_processing_batch_items_batch_lane", "batch_id", "lane"),
        ForeignKeyConstraint(
            ["batch_id", "workspace_id"],
            ["processing_batches.id", "processing_batches.workspace_id"],
            name="fk_processing_batch_items_batch_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["work_item_id", "workspace_id"],
            ["work_items.id", "work_items.workspace_id"],
            name="fk_processing_batch_items_work_item_workspace",
            ondelete="CASCADE",
        ),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    work_item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    lane: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    lane_reasons: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class DispatchPolicy(Base):
    """A workspace's thresholds for the three dispatch lanes. No row means the defaults."""

    __tablename__ = "dispatch_policies"

    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", name="pk_dispatch_policies"),
        CheckConstraint(
            "straight_through_min_confidence >= 0 AND straight_through_min_confidence <= 1",
            name="ck_dispatch_policies_straight_through_ratio",
        ),
        CheckConstraint(
            "review_min_confidence >= 0 AND review_min_confidence <= 1",
            name="ck_dispatch_policies_review_ratio",
        ),
        CheckConstraint(
            "review_min_confidence <= straight_through_min_confidence",
            name="ck_dispatch_policies_review_below_straight_through",
        ),
        ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"],
            name="fk_dispatch_policies_workspace_id_workspaces", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"],
            name="fk_dispatch_policies_updated_by_user_id_users", ondelete="SET NULL",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    straight_through_min_confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default=text("0.9000")
    )
    review_min_confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default=text("0.6000")
    )
    require_required_fields: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    tag_documents: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ExportPackage(Base):
    """A zip of a batch's extracted data (and optionally its files), with a SHA-256 manifest."""

    __tablename__ = "export_packages"

    __table_args__ = (
        CheckConstraint(_in("status", PACKAGE_STATUSES), name="ck_export_packages_status"),
        CheckConstraint(
            "(status = 'READY') = (package_sha256 IS NOT NULL AND storage_key IS NOT NULL)",
            name="ck_export_packages_ready_has_digest",
        ),
        CheckConstraint(
            "(status = 'FAILED') = (error_code IS NOT NULL)",
            name="ck_export_packages_failed_has_code",
        ),
        CheckConstraint(
            "package_sha256 IS NULL OR package_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_export_packages_package_sha256_hex",
        ),
        CheckConstraint(
            "manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_export_packages_manifest_sha256_hex",
        ),
        CheckConstraint("jsonb_typeof(manifest) = 'array'", name="ck_export_packages_manifest_array"),
        CheckConstraint("jsonb_typeof(work_item_ids) = 'array'", name="ck_export_packages_ids_array"),
        CheckConstraint("char_length(btrim(name)) > 0", name="ck_export_packages_name_not_blank"),
        Index("ix_export_packages_workspace_created", "workspace_id", "created_at"),
        Index("ix_export_packages_batch", "batch_id"),
        ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"],
            name="fk_export_packages_workspace_id_workspaces", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_export_packages_organization_id_organizations", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["batch_id"], ["processing_batches.id"],
            name="fk_export_packages_batch_id_processing_batches", ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name="fk_export_packages_created_by_user_id_users", ondelete="SET NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'QUEUED'"))
    include_originals: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    #: The documents the package covers, fixed when it was requested.
    work_item_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    package_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    manifest_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    #: [{path, sha256, size}] for every file in the archive except SHA256SUMS itself.
    manifest: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    storage_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    download_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_downloaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class SchemaHealingEvent(Base):
    """One document's fields renamed and retyped to its schema, with the original kept for undo."""

    __tablename__ = "schema_healing_events"

    __table_args__ = (
        CheckConstraint("jsonb_typeof(changes) = 'array'", name="ck_schema_healing_events_changes_array"),
        CheckConstraint(
            "reverted_by_user_id IS NULL OR reverted_at IS NOT NULL",
            name="ck_schema_healing_events_revert_pair",
        ),
        Index("ix_schema_healing_events_work_item", "work_item_id", "applied_at"),
        Index("ix_schema_healing_events_batch", "batch_id"),
        ForeignKeyConstraint(
            ["work_item_id", "workspace_id"],
            ["work_items.id", "work_items.workspace_id"],
            name="fk_schema_healing_events_work_item_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["batch_id"], ["processing_batches.id"],
            name="fk_schema_healing_events_batch_id_processing_batches", ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["applied_by_user_id"], ["users.id"],
            name="fk_schema_healing_events_applied_by_user_id_users", ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["reverted_by_user_id"], ["users.id"],
            name="fk_schema_healing_events_reverted_by_user_id_users", ondelete="SET NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    work_item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    schema_key: Mapped[str] = mapped_column(String(96), nullable=False)
    changes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    original_entities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: SHA-256 of the fields healing wrote; undo refuses when the fields no longer match it.
    result_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    reverted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reverted_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
