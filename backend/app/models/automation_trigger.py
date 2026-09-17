"""ARCH-37 — which internal events a rule listens to.

One row per (rule, event). The automation handler resolves rules by joining
this table on the event it was handed; before ARCH-37 it looked the event up in
a three-entry dictionary and three of the four triggers the console offered
could never match.

`workspace_id` is denormalised so the resolution query is a single index scan
on (workspace_id, event_type). `trg_automation_rule_triggers_workspace` keeps
it equal to the rule's workspace, so the copy cannot drift into a cross-tenant
match.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.automation_events import LEGACY_RULE_EVENT_TYPES
from app.db.base import Base

_LEGACY_SQL = ", ".join(f"'{value}'" for value in LEGACY_RULE_EVENT_TYPES)


class AutomationRuleTrigger(Base):
    __tablename__ = "automation_rule_triggers"

    __table_args__ = (
        CheckConstraint(
            f"event_type LIKE 'trigger.%' OR event_type IN ({_LEGACY_SQL})",
            name="ck_automation_rule_triggers_event_known",
        ),
        Index(
            "ix_automation_rule_triggers_workspace_event",
            "workspace_id",
            "event_type",
        ),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation_rules.id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:
        return f"<AutomationRuleTrigger {self.rule_id} {self.event_type}>"


__all__ = ["AutomationRuleTrigger"]
