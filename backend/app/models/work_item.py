"""
Database representation of the Work Item entity for FlowPilot AI.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING, Union
from sqlalchemy import DateTime, String, Text, Integer, ForeignKey, JSON, Index, text
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID
from app.db.base import Base, UUIDMixin, TimestampMixin
from app.schemas.work_item import WorkItemStatus

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.automation import AutomationLog
    from app.models.notification import Notification
    from app.models.assistant import Conversation
    from app.models.workspace import Workspace
    from app.models.uploaded_file import UploadedFile


class WorkItem(Base, UUIDMixin, TimestampMixin):
    """
    Persistent representation of a business document (Work Item) within FlowPilot AI.
    """
    __tablename__ = "work_items"

    __table_args__ = (
        Index("ix_work_items_workspace_created", "workspace_id", "created_at"),
        Index("ix_work_items_workspace_status", "workspace_id", "status"),
        Index("ix_work_items_page_count", "page_count", postgresql_where=text("page_count IS NOT NULL")),
        Index("ix_work_items_workspace_pipeline_stage", "workspace_id", "pipeline_stage"),
        Index(
            "ix_work_items_stage_stuck",
            "stage_updated_at",
            postgresql_where=text("pipeline_stage IN ('QUEUED','EXTRACTING','EXTRACTED','ENRICHING')"),
        ),
        Index(
            "uq_work_items_uploaded_file_id",
            "uploaded_file_id",
            unique=True,
            postgresql_where=text("uploaded_file_id IS NOT NULL"),
        ),
    )

    original_filename: Mapped[str] = mapped_column(
        String(255), 
        nullable=False
    )
    stored_filename: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
        doc="Storage key in object storage driver.",
    )
    file_type: Mapped[str] = mapped_column(
        String(100), 
        nullable=False
    )
    file_size: Mapped[int] = mapped_column(
        Integer, 
        nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default=WorkItemStatus.QUEUED.value,
        nullable=False
    )
    
    # --- ARCH-10 Step 7 Pipeline State Machine ---------------------------
    pipeline_stage: Mapped[str] = mapped_column(
        PGEnum(
            "QUEUED",
            "EXTRACTING",
            "EXTRACTED",
            "ENRICHING",
            "COMPLETED",
            "FAILED",
            "QUOTA_BLOCKED",
            name="work_item_pipeline_stage",
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
        default="QUEUED",
        server_default="QUEUED",
    )
    stage_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    failure_stage: Mapped[Optional[str]] = mapped_column(
        String(32),
        nullable=True,
    )
    failure_reason: Mapped[Optional[str]] = mapped_column(
        String(1000),
        nullable=True,
    )

    summary: Mapped[Union[str, None]] = mapped_column(
        Text,
        nullable=True,
    )
    extracted_entities: Mapped[Union[dict[str, Any], None]] = mapped_column(
        JSON, 
        nullable=True
    )

    # --- ARCH-10 Step 5 Linkage ------------------------------------------
    uploaded_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="SET NULL"),
        nullable=True,
    )
    page_count: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    extracted_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    extraction_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
    )
    
    # --- Scope: the workspace owns this document -------------------------
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    # --- Attribution: who uploaded it ------------------------------------
    created_by_user_id: Mapped[Union[uuid.UUID, None]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship("Workspace")
    created_by: Mapped[Union["User", None]] = relationship("User")
    uploaded_file: Mapped[Optional["UploadedFile"]] = relationship("UploadedFile")

    # Child relationships
    automation_logs: Mapped[list["AutomationLog"]] = relationship(
        "AutomationLog",
        back_populates="work_item",
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        back_populates="work_item",
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation",
        back_populates="work_item",
        cascade="all, delete-orphan",
        passive_deletes=True  
    )