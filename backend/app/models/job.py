"""
Database representation of the Processing Job entity for FlowPilot AI.

Tracks discrete execution runs, progress percentages, error logs, and performance metadata 
linked to parent Work Items.
"""

import uuid
from typing import Any, TYPE_CHECKING, Union
from sqlalchemy import String, Integer, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID
from app.db.base import Base, UUIDMixin, TimestampMixin
from app.schemas.job import JobStatus

if TYPE_CHECKING:
    from app.models.work_item import WorkItem


class ProcessingJob(Base, UUIDMixin, TimestampMixin):
    """
    Persistent representation of a single background pipeline execution run.
    
    Houses progress logs, retry counters, and system tracebacks for failure isolation.
    """
    __tablename__ = "processing_jobs"

    progress: Mapped[int] = mapped_column(
        Integer, 
        default=0, 
        nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default=JobStatus.PENDING.value,
        index=True,
        nullable=False
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, 
        default=0, 
        nullable=False
    )
    error_message: Mapped[Union[str, None]] = mapped_column(
        String(5000),
        nullable=True
    )
    
    # Avoids using SQLAlchemy's reserved 'metadata' attribute
    execution_metadata: Mapped[Union[dict[str, Any], None]] = mapped_column(
        JSON, 
        nullable=True
    )

    # Database foreign key link referencing the parent Work Item
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )

    # Bidirectional SQLAlchemy relationship referencing the parent WorkItem object
    work_item: Mapped["WorkItem"] = relationship(
        "WorkItem", 
        back_populates="jobs"
    )