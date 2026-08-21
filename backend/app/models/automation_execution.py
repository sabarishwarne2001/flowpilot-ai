"""ARCH-13 Step 13.3 — execution records and the A6 budget.

The execution carries its own budget, in its own row. F5 rejected the two
obvious alternatives:

  * **Not** a `spend_limits` row per execution. `spend_limits` is a
    tenant-level contract; writing one per execution would put millions of
    rows in a table the hot path reads under an advisory lock.
  * **Not** an org-level `limit_key`. ARCH-14's bounded read is per
    organization per period; it cannot answer "how much has *this execution*
    spent" without a per-execution aggregate, which is a new read.

So: `budget_cost_micros` is the ceiling, `spent_cost_micros` is the running
total, incremented inside the same transaction as the `usage_events` row that
spent it. The check is a single indexed row read, not an aggregate — and it is
a CHECK constraint, so a refactor that forgets the comparison still cannot
overspend.

The org-level ARCH-14 ceilings still apply underneath. An execution budget can
only ever be *more* restrictive; it is never a way past `TOTAL_COST_KEY`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class AutomationExecutionStatus(str, PyEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    #: A7. This rule already executed in this causal chain.
    SUPPRESSED_CYCLE = "SUPPRESSED_CYCLE"
    #: A7 backstop. The chain exceeded AUTOMATION_MAX_DEPTH without repeating
    #: a rule — a long chain of distinct rules, which is not a cycle.
    SUPPRESSED_DEPTH = "SUPPRESSED_DEPTH"
    #: A6. The execution hit `budget_cost_micros` mid-graph.
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class AutomationNodeRunStatus(str, PyEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    #: A branch not taken, or a node skipped after a HALT.
    SKIPPED = "SKIPPED"
    TIMED_OUT = "TIMED_OUT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


TERMINAL_EXECUTION_STATUSES: tuple[AutomationExecutionStatus, ...] = (
    AutomationExecutionStatus.COMPLETED,
    AutomationExecutionStatus.FAILED,
    AutomationExecutionStatus.TIMED_OUT,
    AutomationExecutionStatus.SUPPRESSED_CYCLE,
    AutomationExecutionStatus.SUPPRESSED_DEPTH,
    AutomationExecutionStatus.BUDGET_EXHAUSTED,
)

SUPPRESSED_STATUSES: tuple[AutomationExecutionStatus, ...] = (
    AutomationExecutionStatus.SUPPRESSED_CYCLE,
    AutomationExecutionStatus.SUPPRESSED_DEPTH,
)

EXECUTION_STATUS_ENUM_NAME = "automation_execution_status"
NODE_RUN_STATUS_ENUM_NAME = "automation_node_run_status"

_TERMINAL_SQL = ", ".join(f"'{s.value}'" for s in TERMINAL_EXECUTION_STATUSES)


class AutomationExecution(Base, UUIDMixin, TimestampMixin):
    """One rule, one triggering event, one run."""

    __tablename__ = "automation_executions"

    __table_args__ = (
        CheckConstraint(
            "spent_cost_micros <= budget_cost_micros",
            name="ck_automation_executions_spend_within_budget",
        ),
        CheckConstraint(
            "budget_cost_micros >= 0 AND spent_cost_micros >= 0",
            name="ck_automation_executions_costs_non_negative",
        ),
        CheckConstraint(
            f"(status = 'RUNNING'::{EXECUTION_STATUS_ENUM_NAME}) = "
            "(deadline_at IS NOT NULL)",
            name="ck_automation_executions_deadline_matches_status",
        ),
        CheckConstraint(
            f"(status IN ({_TERMINAL_SQL})) = (completed_at IS NOT NULL)",
            name="ck_automation_executions_completed_at_matches_status",
        ),
        CheckConstraint(
            "depth >= 0 AND depth <= 16",
            name="ck_automation_executions_depth_bounded",
        ),
        CheckConstraint(
            "nodes_executed >= 0 AND nodes_executed <= node_count",
            name="ck_automation_executions_nodes_executed_bounded",
        ),
        CheckConstraint(
            "jsonb_typeof(emitted_event_ids) = 'array'",
            name="ck_automation_executions_emitted_is_array",
        ),
        CheckConstraint(
            "jsonb_typeof(details) = 'object'",
            name="ck_automation_executions_details_is_object",
        ),
        Index(
            "uq_automation_executions_rule_event",
            "rule_id",
            "outbox_event_id",
            unique=True,
            postgresql_where=text("outbox_event_id IS NOT NULL"),
        ),
        Index(
            "ix_automation_executions_correlation_rule",
            "correlation_id",
            "rule_id",
        ),
        Index(
            "ix_automation_executions_running_deadline",
            "deadline_at",
            postgresql_where=text(
                f"status = 'RUNNING'::{EXECUTION_STATUS_ENUM_NAME}"
            ),
        ),
        Index(
            "ix_automation_executions_workspace_created",
            "workspace_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_automation_executions_emitted_event_ids",
            "emitted_event_ids",
            postgresql_using="gin",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation_rules.id", ondelete="RESTRICT"),
        nullable=False,
    )
    work_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    outbox_event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("outbox_events.id", ondelete="SET NULL"),
        nullable=True,
    )

    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[AutomationExecutionStatus] = mapped_column(
        PGEnum(
            AutomationExecutionStatus,
            name=EXECUTION_STATUS_ENUM_NAME,
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
    )

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deadline_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ---- A6 -----------------------------------------------------------
    budget_cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spent_cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )

    node_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nodes_executed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actions_executed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    emitted_event_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    node_runs: Mapped[list["AutomationNodeRun"]] = relationship(
        "AutomationNodeRun",
        back_populates="execution",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AutomationNodeRun.sequence",
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_EXECUTION_STATUSES

    @property
    def is_suppressed(self) -> bool:
        return self.status in SUPPRESSED_STATUSES

    @property
    def remaining_budget_micros(self) -> int:
        return max(0, int(self.budget_cost_micros) - int(self.spent_cost_micros))

    def scope_for(self, node_key: str) -> str:
        """The ARCH-14 reservation scope for one node of this execution."""
        return f"llm:automation:{self.rule_id}:{self.id}:{node_key}"

    def __repr__(self) -> str:
        return (
            f"<AutomationExecution {self.id} rule={self.rule_id} "
            f"status={self.status.value if self.status else None} "
            f"spent={self.spent_cost_micros}/{self.budget_cost_micros}>"
        )


class AutomationNodeRun(Base, UUIDMixin, TimestampMixin):
    """One node, one execution. Digests, not payloads."""

    __tablename__ = "automation_node_runs"

    __table_args__ = (
        CheckConstraint(
            "cost_micros >= 0", name="ck_automation_node_runs_cost_non_negative"
        ),
        CheckConstraint(
            "sequence >= 0", name="ck_automation_node_runs_sequence_non_negative"
        ),
        CheckConstraint(
            "input_digest IS NULL OR input_digest LIKE 'sha256:%'",
            name="ck_automation_node_runs_input_digest_prefixed",
        ),
        CheckConstraint(
            "output_digest IS NULL OR output_digest LIKE 'sha256:%'",
            name="ck_automation_node_runs_output_digest_prefixed",
        ),
        UniqueConstraint(
            "execution_id",
            "sequence",
            name="uq_automation_node_runs_execution_sequence",
        ),
        Index(
            "ix_automation_node_runs_execution_sequence",
            "execution_id",
            "sequence",
        ),
    )

    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_key: Mapped[str] = mapped_column(String(64), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[AutomationNodeRunStatus] = mapped_column(
        PGEnum(
            AutomationNodeRunStatus,
            name=NODE_RUN_STATUS_ENUM_NAME,
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
    )

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: `sha256:<hex>` — 7 + 64 characters.
    input_digest: Mapped[Optional[str]] = mapped_column(String(71), nullable=True)
    output_digest: Mapped[Optional[str]] = mapped_column(String(71), nullable=True)

    cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    execution: Mapped[AutomationExecution] = relationship(
        "AutomationExecution", back_populates="node_runs"
    )

    def __repr__(self) -> str:
        return (
            f"<AutomationNodeRun {self.node_key} seq={self.sequence} "
            f"status={self.status.value if self.status else None}>"
        )


__all__ = [
    "EXECUTION_STATUS_ENUM_NAME",
    "NODE_RUN_STATUS_ENUM_NAME",
    "SUPPRESSED_STATUSES",
    "TERMINAL_EXECUTION_STATUSES",
    "AutomationExecution",
    "AutomationExecutionStatus",
    "AutomationNodeRun",
    "AutomationNodeRunStatus",
]