"""
Database representation of the Work Item entity for FlowPilot AI.
"""

import uuid
from typing import Any, TYPE_CHECKING, Union
from sqlalchemy import String, Text, Integer, ForeignKey, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID
from app.db.base import Base, UUIDMixin, TimestampMixin
from app.schemas.work_item import WorkItemStatus

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.job import ProcessingJob
    from app.models.automation import AutomationLog
    from app.models.notification import Notification
    from app.models.assistant import Conversation
    from app.models.workspace import Workspace


class WorkItem(Base, UUIDMixin, TimestampMixin):
    """
    Persistent representation of a business document (Work Item) within FlowPilot AI.
    """
    __tablename__ = "work_items"

    __table_args__ = (
        Index("ix_work_items_workspace_created", "workspace_id", "created_at"),
        Index("ix_work_items_workspace_status", "workspace_id", "status"),
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
        doc=(
            "Filesystem storage key. The unique index is a data-integrity "
            "guarantee, not an optimisation."
        ),
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
    summary: Mapped[Union[str, None]] = mapped_column(
        Text,
        nullable=True,
        doc=(
            "Unbounded AI-generated prose. Declared Text rather than String "
            "so the model states the absence of a length bound explicitly."
        ),
    )
    extracted_entities: Mapped[Union[dict[str, Any], None]] = mapped_column(
        JSON, 
        nullable=True
    )
    
    # --- Scope: the workspace owns this document -------------------------
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    # --- Attribution: who uploaded it. Nullable by necessity -------------
    created_by_user_id: Mapped[Union[uuid.UUID, None]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc=(
            "Author, not scope. Nullable because ON DELETE SET NULL cannot "
            "fire against a NOT NULL column — deleting a user would raise "
            "instead of orphaning the attribution. A NULL here reads as "
            "'uploaded by a former member', which is the correct semantics."
        ),
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship("Workspace")

    created_by: Mapped[Union["User", None]] = relationship("User")

    # Bidirectional SQLAlchemy relationships referencing child objects
    jobs: Mapped[list["ProcessingJob"]] = relationship(
        "ProcessingJob",
        back_populates="work_item",
        cascade="all, delete-orphan",
        passive_deletes=True  
    )

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