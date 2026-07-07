"""
Database representation of the Work Item entity for FlowPilot AI.

Manages original and storage filename mapping, active processing pipeline states, 
AI-generated extraction schemas, and bidirectional relationships to Users, Jobs, Logs, 
Alerts, and Conversation memories.
"""

import uuid
from typing import Any, TYPE_CHECKING, Union
from sqlalchemy import String, Integer, ForeignKey, JSON
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


class WorkItem(Base, UUIDMixin, TimestampMixin):
    """
    Persistent representation of a business document (Work Item) within FlowPilot AI.
    
    Acts as the central business document entity powering AI processing, semantic retrieval, automation workflows, and assistant conversations.
    """
    __tablename__ = "work_items"

    original_filename: Mapped[str] = mapped_column(
        String(255), 
        nullable=False
    )
    stored_filename: Mapped[str] = mapped_column(
        String(255), 
        nullable=False
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
        index=True,
        nullable=False
    )
    summary: Mapped[Union[str, None]] = mapped_column(
        String, 
        nullable=True
    )
    extracted_entities: Mapped[Union[dict[str, Any], None]] = mapped_column(
        JSON, 
        nullable=True
    )
    
    # Ownership foreign key relation referencing the parent User account
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )

    # Bidirectional SQLAlchemy relationship referencing the parent User object
    user: Mapped["User"] = relationship(
        "User", 
        back_populates="work_items"
    )

    # Bidirectional SQLAlchemy relationship referencing child Processing Jobs
    jobs: Mapped[list["ProcessingJob"]] = relationship(
        "ProcessingJob",
        back_populates="work_item",
        cascade="all, delete-orphan",
        passive_deletes=True  
    )

    # Bidirectional SQLAlchemy relationship mapping child Automation Logs
    automation_logs: Mapped[list["AutomationLog"]] = relationship(
        "AutomationLog",
        back_populates="work_item",
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    # Bidirectional SQLAlchemy relationship mapping child in-app Notifications
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        back_populates="work_item",
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    # Bidirectional SQLAlchemy relationship mapping child Conversation sessions
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation",
        back_populates="work_item",
        cascade="all, delete-orphan",
        passive_deletes=True  
    )