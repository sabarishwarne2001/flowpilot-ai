"""
SQLAlchemy Declarative Base and shared model mixins for FlowPilot AI.

Defines standard metadata containers and provides mixins for automated 
primary keys (UUIDv4) and audit tracking timestamps.
"""

import uuid
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime, UUID

class Base(DeclarativeBase):
    """
    Common base class for all relational database models.
    Hosts application-wide metadata collection for schema migrations.
    """
    pass

class UUIDMixin:
    """
    Model mixin that implements a secure, database-native UUIDv4 primary key.
    
    Places the id property at the top of table mappings using sort_order.
    """
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
        sort_order=-100  # Places primary key at the top of generated tables
    )

class TimestampMixin:
    """
    Model mixin that implements timezone-aware audit timestamp tracking.
    
    Delegates recording behaviors strictly to database server functions.
    Places audit tracking columns at the end of table mappings using sort_order.
    """
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        sort_order=100  # Positions column at the end of generated tables
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        sort_order=101  # Positions column after created_at
    )