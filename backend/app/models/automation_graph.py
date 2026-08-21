"""ARCH-13 Step 13.4 — node graph persistence."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.automation import AutomationRule


NODE_TYPES: frozenset[str] = frozenset(
    {"trigger", "condition", "action", "branch", "join"}
)

BRANCH_LABELS: frozenset[str] = frozenset({"default", "true", "false"})

GRAPH_VERSION_FLAT: int = 0
GRAPH_VERSION_DAG: int = 1


class AutomationNode(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "automation_nodes"

    __table_args__ = (
        CheckConstraint(
            "node_type IN ('trigger', 'condition', 'action', 'branch', 'join')",
            name="ck_automation_nodes_type_known",
        ),
        CheckConstraint(
            "jsonb_typeof(config) = 'object'",
            name="ck_automation_nodes_config_is_object",
        ),
        CheckConstraint(
            "topological_order >= 0",
            name="ck_automation_nodes_order_non_negative",
        ),
        CheckConstraint(
            r"node_key <> '' AND node_key !~ '\s'",
            name="ck_automation_nodes_key_shape",
        ),
        UniqueConstraint(
            "rule_id", "node_key", name="uq_automation_nodes_rule_node_key"
        ),
        UniqueConstraint(
            "rule_id", "topological_order", name="uq_automation_nodes_rule_order"
        ),
        Index("ix_automation_nodes_rule_order", "rule_id", "topological_order"),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation_rules.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_key: Mapped[str] = mapped_column(String(64), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    position: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    topological_order: Mapped[int] = mapped_column(Integer, nullable=False)

    rule: Mapped["AutomationRule"] = relationship(
        "AutomationRule", back_populates="nodes"
    )

    def __repr__(self) -> str:
        return (
            f"<AutomationNode {self.node_key} type={self.node_type} "
            f"order={self.topological_order}>"
        )


class AutomationEdge(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "automation_edges"

    __table_args__ = (
        CheckConstraint(
            "branch IN ('default', 'true', 'false')",
            name="ck_automation_edges_branch_known",
        ),
        CheckConstraint(
            "from_node_key <> to_node_key",
            name="ck_automation_edges_no_self_loop",
        ),
        UniqueConstraint(
            "rule_id",
            "from_node_key",
            "to_node_key",
            "branch",
            name="uq_automation_edges_rule_from_to_branch",
        ),
        ForeignKeyConstraint(
            ["rule_id", "from_node_key"],
            ["automation_nodes.rule_id", "automation_nodes.node_key"],
            name="fk_automation_edges_from_node",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["rule_id", "to_node_key"],
            ["automation_nodes.rule_id", "automation_nodes.node_key"],
            name="fk_automation_edges_to_node",
            ondelete="CASCADE",
        ),
        Index("ix_automation_edges_rule_from", "rule_id", "from_node_key"),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation_rules.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_node_key: Mapped[str] = mapped_column(String(64), nullable=False)
    to_node_key: Mapped[str] = mapped_column(String(64), nullable=False)
    branch: Mapped[str] = mapped_column(
        String(32), nullable=False, default="default", server_default=text("'default'")
    )

    def __repr__(self) -> str:
        return (
            f"<AutomationEdge {self.from_node_key} -[{self.branch}]-> "
            f"{self.to_node_key}>"
        )


__all__ = [
    "BRANCH_LABELS",
    "GRAPH_VERSION_DAG",
    "GRAPH_VERSION_FLAT",
    "NODE_TYPES",
    "AutomationEdge",
    "AutomationNode",
]