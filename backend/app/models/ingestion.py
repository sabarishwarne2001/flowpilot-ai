"""ARCH-38 — batch ingestion, upload sessions, tags, presets and retention holds.

Every table here is workspace- or organization-scoped. The two child tables
carry composite foreign keys onto `(id, workspace_id)` of their parent rather
than a bare `id`, so a row whose workspace disagrees with its parent's has no
parent to point at. That is the database half of the cross-workspace gate; the
service half lives in `app/services/ingestion/`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.work_item import WorkItem


# --------------------------------------------------------------------------
# Vocabularies. Each mirrors a CHECK in arch38_step1_batches; verify_arch38
# asserts the two agree, so a value added here and not there fails the build
# rather than the first INSERT in production.
# --------------------------------------------------------------------------

BATCH_STATUSES: tuple[str, ...] = (
    "OPEN",
    "UPLOADING",
    "PROCESSING",
    "COMPLETED",
    "COMPLETED_WITH_ERRORS",
    "CANCELLED",
)
TERMINAL_BATCH_STATUSES: frozenset[str] = frozenset(
    {"COMPLETED", "COMPLETED_WITH_ERRORS", "CANCELLED"}
)
ITEM_STATUSES: tuple[str, ...] = (
    "PENDING",
    "UPLOADING",
    "UPLOADED",
    "PROCESSING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
)
SESSION_STATUSES: tuple[str, ...] = ("ACTIVE", "COMPLETED", "ABORTED", "EXPIRED")
BATCH_SOURCES: tuple[str, ...] = ("FILES", "FOLDER", "ARCHIVE")

TAG_PATTERN: str = r"^[a-z0-9][a-z0-9_-]{0,47}$"

#: Platform presets carry organization_id IS NULL. The uniqueness index
#: collapses that to this value so two platform presets cannot share a
#: (document_type, version): NULL does not collide with NULL in an index.
PLATFORM_PRESET_SCOPE: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")


class IngestionBatch(Base):
    """One drop: a multi-file selection, a folder, or an expanded archive."""

    __tablename__ = "ingestion_batches"

    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_ingestion_batches_id_workspace_id"),
        Index("ix_ingestion_batches_workspace_created", "workspace_id", "created_at"),
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_ingestion_batches_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_ingestion_batches_created_by_user_id_users",
            ondelete="SET NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="FILES")
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    items: Mapped[list["IngestionBatchItem"]] = relationship(
        "IngestionBatchItem",
        back_populates="batch",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_BATCH_STATUSES


class UploadSession(Base):
    """A resumable multipart upload.

    `parts_received` is the resumption record, and it lives here rather than in
    the browser precisely so a page reload does not lose it. Every append goes
    through `upload_session_service._append_part`, which takes a row lock: the
    client uploads three parts at a time, and a read-modify-write on an int[]
    without a lock loses parts under exactly that concurrency.
    """

    __tablename__ = "upload_sessions"

    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_upload_sessions_id_workspace_id"),
        Index("ix_upload_sessions_workspace_status", "workspace_id", "status"),
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_upload_sessions_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_upload_sessions_organization_id_organizations",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_upload_sessions_created_by_user_id_users",
            ondelete="SET NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False
    )
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    minio_upload_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    part_size: Mapped[int] = mapped_column(Integer, nullable=False)
    total_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    parts_received: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, server_default=text("'{}'::int[]")
    )
    expected_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IngestionBatchItem(Base):
    """One file inside a batch, with its own status.

    `error_code` is NOT NULL whenever the status is FAILED (database CHECK), so
    "something failed and nobody recorded why" is not a state this table can
    hold.
    """

    __tablename__ = "ingestion_batch_items"

    __table_args__ = (
        UniqueConstraint(
            "batch_id", "client_key", name="uq_ingestion_batch_items_batch_client_key"
        ),
        Index("ix_ingestion_batch_items_batch_status", "batch_id", "status"),
        ForeignKeyConstraint(
            ["batch_id", "workspace_id"],
            ["ingestion_batches.id", "ingestion_batches.workspace_id"],
            name="fk_ingestion_batch_items_batch_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["work_item_id", "workspace_id"],
            ["work_items.id", "work_items.workspace_id"],
            name="fk_ingestion_batch_items_work_item_workspace",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["upload_session_id", "workspace_id"],
            ["upload_sessions.id", "upload_sessions.workspace_id"],
            name="fk_ingestion_batch_items_session_workspace",
            ondelete="SET NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    client_key: Mapped[str] = mapped_column(String(200), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    upload_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    work_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    batch: Mapped["IngestionBatch"] = relationship(
        "IngestionBatch", back_populates="items"
    )


class WorkItemTag(Base):
    """A lowercase label on a document, scoped to its workspace."""

    __tablename__ = "work_item_tags"

    __table_args__ = (
        PrimaryKeyConstraint("work_item_id", "tag", name="pk_work_item_tags"),
        Index("ix_work_item_tags_workspace_tag", "workspace_id", "tag"),
        ForeignKeyConstraint(
            ["work_item_id", "workspace_id"],
            ["work_items.id", "work_items.workspace_id"],
            name="fk_work_item_tags_work_item_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_work_item_tags_created_by_user_id_users",
            ondelete="SET NULL",
        ),
    )

    work_item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tag: Mapped[str] = mapped_column(String(48), nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class DocumentSchemaPreset(Base):
    """A vertical pack: field list, suggested assertions, redaction profile.

    `organization_id IS NULL` is a platform preset, visible to every tenant and
    editable by none.
    """

    __tablename__ = "document_schema_presets"

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_document_schema_presets_organization_id_organizations",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    industry: Mapped[str] = mapped_column(String(32), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    assertions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    classifier_hints: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    redaction_profile: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    @property
    def is_platform(self) -> bool:
        return self.organization_id is None


class WorkspaceSchemaPreset(Base):
    """A preset applied to a workspace, and separately enabled.

    Applying and enabling are two acts on purpose: the gallery applies, the
    reviewer reads the field list, the assertions and the redaction profile,
    and only then enables. A single boolean would collapse the review step the
    specification asks for.
    """

    __tablename__ = "workspace_schema_presets"

    __table_args__ = (
        PrimaryKeyConstraint(
            "workspace_id", "preset_id", name="pk_workspace_schema_presets"
        ),
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_workspace_schema_presets_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["preset_id"],
            ["document_schema_presets.id"],
            name="fk_workspace_schema_presets_preset_id_presets",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["applied_by_user_id"],
            ["users.id"],
            name="fk_workspace_schema_presets_applied_by_user_id_users",
            ondelete="SET NULL",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    preset_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    applied_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    enabled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RetentionHold(Base):
    """A named, audited reason a document may not be deleted.

    ARCH-20 ships an age floor and no hold. This is the hold. `released_at IS
    NULL` is the active predicate, and both partial indexes are built on it, so
    the check `bulk_service` runs before every delete is an index lookup rather
    than a scan.
    """

    __tablename__ = "retention_holds"

    __table_args__ = (
        Index(
            "ix_retention_holds_active_work_item",
            "work_item_id",
            postgresql_where=text("released_at IS NULL AND work_item_id IS NOT NULL"),
        ),
        Index(
            "ix_retention_holds_active_workspace",
            "workspace_id",
            postgresql_where=text("released_at IS NULL AND workspace_id IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_retention_holds_organization_id_organizations",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_retention_holds_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name="fk_retention_holds_work_item_id_work_items",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["placed_by_user_id"],
            ["users.id"],
            name="fk_retention_holds_placed_by_user_id_users",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["released_by_user_id"],
            ["users.id"],
            name="fk_retention_holds_released_by_user_id_users",
            ondelete="SET NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False
    )
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    work_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    reference: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    placed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    released_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )

    @property
    def is_active(self) -> bool:
        return self.released_at is None


__all__ = [
    "BATCH_SOURCES",
    "BATCH_STATUSES",
    "DocumentSchemaPreset",
    "ITEM_STATUSES",
    "IngestionBatch",
    "IngestionBatchItem",
    "PLATFORM_PRESET_SCOPE",
    "RetentionHold",
    "SESSION_STATUSES",
    "TAG_PATTERN",
    "TERMINAL_BATCH_STATUSES",
    "UploadSession",
    "WorkItemTag",
    "WorkspaceSchemaPreset",
]
