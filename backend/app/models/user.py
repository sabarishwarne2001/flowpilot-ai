"""
Database representation of the system User entity for FlowPilot AI.

Defines account credentials, authentication indexes, platform authorization flags, 
and bidirectional relationship mappings targeting work items, rules, notifications, 
and conversational memory blocks.
"""

from typing import TYPE_CHECKING
from sqlalchemy import String, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, UUIDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.work_item import WorkItem
    from app.models.automation import AutomationRule
    from app.models.notification import Notification
    from app.models.assistant import Conversation
    from app.models.email_settings import EmailSettings


class User(Base, UUIDMixin, TimestampMixin):
    """
    Persistent representation of a user identity within FlowPilot AI.
    
    Inherits UUID primary keys and automated timezone audit tracking timestamps.
    """
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )
    is_superuser: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    # Bidirectional SQLAlchemy relationship mapping child Work Item entities
    work_items: Mapped[list["WorkItem"]] = relationship(
        "WorkItem",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    # Bidirectional SQLAlchemy relationship mapping child Automation Rules
    automation_rules: Mapped[list["AutomationRule"]] = relationship(
        "AutomationRule",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    # Bidirectional SQLAlchemy relationship mapping child in-app Notifications
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    # Bidirectional SQLAlchemy relationship mapping user SMTP configuration
    email_settings: Mapped["EmailSettings | None"] = relationship(
        "EmailSettings",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    # Bidirectional SQLAlchemy relationship mapping child Conversation sessions
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True 
    )