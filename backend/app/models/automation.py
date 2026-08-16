"""
Database representation of Automation Rules and Audit History Logs for FlowPilot AI.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Union

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace


class AutomationRule(Base, UUIDMixin, TimestampMixin):
    """
    Persistent representation of a user-defined automation rule.
    """

    __tablename__ = "automation_rules"

    __table_args__ = (
        Index("ix_automation_rules_workspace_active", "workspace_id", "is_active"),
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        index=True,
    )

    event: Mapped[str] = mapped_column(
        String(50),
        index=True,
        nullable=False,
    )

    conditions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    logic_operator: Mapped[str] = mapped_column(
        PGEnum(
            "AND",
            "OR",
            name="logic_operator",
            create_type=False,
        ),
        nullable=False,
        default="AND",
    )

    actions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    created_by_user_id: Mapped[Union[uuid.UUID, None]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    workspace: Mapped[Workspace] = relationship("Workspace")

    created_by: Mapped[Union[User, None]] = relationship("User")

    logs: Mapped[list[AutomationLog]] = relationship(
        "AutomationLog",
        back_populates="rule",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class AutomationLog(Base, UUIDMixin, TimestampMixin):
    """
    Stores the execution history of automation rule runs.
    """

    __tablename__ = "automation_logs"

    __table_args__ = (
        Index("ix_automation_logs_workspace_created", "workspace_id", "created_at"),
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    log_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    workspace: Mapped[Workspace] = relationship("Workspace")

    rule: Mapped[AutomationRule] = relationship(
        "AutomationRule",
        back_populates="logs",
    )

    work_item: Mapped[WorkItem] = relationship(
        "WorkItem",
        back_populates="automation_logs",
        passive_deletes=True,
    )