"""
Database representation of Automation Rules and Audit History Logs for FlowPilot AI.

Manages user-facing trigger-action rule definitions, extensible JSON configuration
mappings, and execution audit histories.
"""

import uuid
from typing import Any, TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.work_item import WorkItem


class AutomationRule(Base, UUIDMixin, TimestampMixin):
    """
    Persistent representation of a user-defined automation rule.
    """

    __tablename__ = "automation_rules"

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    event: Mapped[str] = mapped_column(
        String(50),
        index=True,
        nullable=False,
    )

    field: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    operator: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    value: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    action_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    action_config: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="automation_rules",
    )

    logs: Mapped[list["AutomationLog"]] = relationship(
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

    rule: Mapped["AutomationRule"] = relationship(
        "AutomationRule",
        back_populates="logs",
    )

    work_item: Mapped["WorkItem"] = relationship(
        "WorkItem",
        back_populates="automation_logs",
        passive_deletes=True,
    )