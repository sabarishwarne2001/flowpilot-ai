"""
Database representation of the system User entity for FlowPilot AI.

Defines account credentials, authentication indexes, platform authorization flags,
and bidirectional relationship mappings targeting work items, rules, notifications,
and conversational memory blocks.
"""

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.ai_settings import AISettings
    from app.models.assistant import Conversation
    from app.models.automation import AutomationRule
    from app.models.email_settings import EmailSettings
    from app.models.notification import Notification
    from app.models.organization import OrganizationMember
    from app.models.work_item import WorkItem
    from app.models.workspace import WorkspaceMember


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
        nullable=False,
    )

    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    is_superuser: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # Keep these relationships
    memberships: Mapped[list["WorkspaceMember"]] = relationship(
        "WorkspaceMember",
        back_populates="user",
        foreign_keys="WorkspaceMember.user_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    organization_memberships: Mapped[list["OrganizationMember"]] = relationship(
        "OrganizationMember",
        back_populates="user",
        foreign_keys="OrganizationMember.user_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )