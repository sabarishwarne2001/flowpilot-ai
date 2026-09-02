"""ARCH-13 Step 13.2 — A7 cycle detection and depth bounding.

WHY DEPTH ALONE IS THE WRONG FIX
================================

Rule A updates a field that fires Rule B, which updates a field that fires
Rule A. Under `AUTOMATION_MAX_DEPTH = 5` that runs five times and stops,
silently, every time any document is touched: five wasted executions, five LLM
calls if the rules have LLM actions, and no signal saying "these two rules form
a cycle". The operator sees elevated cost and nothing else.

Depth *truncates* a cycle. It does not detect one.

WHAT THIS MODULE DOES INSTEAD
=============================

`correlation_id` is constant along a causal chain, so "every execution caused
by this upload" is one indexed query. If the same `rule_id` appears twice in
one chain, the second occurrence is a cycle — refuse it, mark the execution
`SUPPRESSED_CYCLE`, name both rules in the record, and log once.

Depth remains, as the backstop for the case this cannot see: a chain of six
*distinct* rules, which is not a cycle and which no repeated-rule check will
catch. That is `SUPPRESSED_DEPTH`, and it is a different status because it is
a different diagnosis — "your rules form a loop" and "your rule chain is deeper
than the configured bound" want different operator responses.

WHY THIS READS EXECUTIONS AND NOT EVENTS
========================================

A cycle is a property of *rules re-running*, not of events recurring. Two
`work_item.field_changed` events in one chain are normal — a rule that sets two
fields emits two. The same rule *executing* twice in one chain is not. So the
lookup is against `automation_executions.rule_id` filtered by
`correlation_id`, which `ix_automation_executions_correlation_rule` serves
directly.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.automation_execution import (
    AutomationExecution,
    AutomationExecutionStatus,
)

logger = logging.getLogger("app.services.automation.cycle_detector")


#: Statuses that count as "this rule already ran in this chain". A prior
#: execution that was itself suppressed does not make the next one a cycle —
#: otherwise one suppression cascades into suppressing every sibling rule.
OCCUPYING_STATUSES: tuple[AutomationExecutionStatus, ...] = (
    AutomationExecutionStatus.QUEUED,
    AutomationExecutionStatus.RUNNING,
    AutomationExecutionStatus.COMPLETED,
    AutomationExecutionStatus.FAILED,
    AutomationExecutionStatus.TIMED_OUT,
    AutomationExecutionStatus.BUDGET_EXHAUSTED,
)


@dataclass(frozen=True)
class Suppression:
    """A refusal to start an execution, with enough detail to diagnose it."""

    status: AutomationExecutionStatus
    reason: str
    #: The prior execution of this rule in this chain, for SUPPRESSED_CYCLE.
    prior_execution_id: Optional[uuid.UUID] = None
    #: The rule that caused the event we are reacting to, for SUPPRESSED_CYCLE.
    #: Naming *both* ends is the difference between "rule X looped" and "rules
    #: X and Y form a ping-pong", and only the second is actionable.
    counterpart_rule_id: Optional[uuid.UUID] = None
    depth: int = 0

    def as_details(self) -> dict[str, object]:
        return {
            "suppressed": self.status.value,
            "reason": self.reason,
            "prior_execution_id": (
                str(self.prior_execution_id) if self.prior_execution_id else None
            ),
            "counterpart_rule_id": (
                str(self.counterpart_rule_id) if self.counterpart_rule_id else None
            ),
            "depth": self.depth,
        }


def prior_execution_in_chain(
    db: Session,
    *,
    rule_id: uuid.UUID,
    correlation_id: uuid.UUID,
) -> Optional[AutomationExecution]:
    """The earliest prior execution of this rule in this causal chain, if any.

    Earliest rather than latest: the record we want to name is the one that
    started the loop, not the most recent lap of it.
    """
    return db.execute(
        select(AutomationExecution)
        .where(
            AutomationExecution.rule_id == rule_id,
            AutomationExecution.correlation_id == correlation_id,
            AutomationExecution.status.in_(OCCUPYING_STATUSES),
        )
        .order_by(AutomationExecution.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()


def rule_of_causing_execution(
    db: Session, *, outbox_event_id: uuid.UUID
) -> Optional[uuid.UUID]:
    """Which rule emitted the event we are reacting to, if a rule did."""
    return db.execute(
        select(AutomationExecution.rule_id).where(
            AutomationExecution.emitted_event_ids.contains([str(outbox_event_id)])
        ).limit(1)
    ).scalar_one_or_none()


def check(
    db: Session,
    *,
    rule_id: uuid.UUID,
    correlation_id: uuid.UUID,
    depth: int,
    causation_id: Optional[uuid.UUID] = None,
    max_depth: Optional[int] = None,
) -> Optional[Suppression]:
    """Decide whether this rule may execute in this chain."""
    ceiling = max_depth if max_depth is not None else settings.AUTOMATION_MAX_DEPTH

    if depth > ceiling:
        suppression = Suppression(
            status=AutomationExecutionStatus.SUPPRESSED_DEPTH,
            reason=(
                f"Causal depth {depth} exceeds AUTOMATION_MAX_DEPTH "
                f"({ceiling}). This chain is a sequence of distinct rules, not "
                "a cycle; if it is legitimate, raise the bound deliberately "
                "rather than letting it truncate."
            ),
            depth=depth,
        )
        logger.warning(
            "automation.suppressed_depth",
            extra={
                "rule_id": str(rule_id),
                "correlation_id": str(correlation_id),
                "depth": depth,
                "max_depth": ceiling,
            },
        )
        return suppression

    prior = prior_execution_in_chain(
        db, rule_id=rule_id, correlation_id=correlation_id
    )
    if prior is None:
        return None

    counterpart = (
        rule_of_causing_execution(db, outbox_event_id=causation_id)
        if causation_id
        else None
    )

    if counterpart and counterpart != rule_id:
        reason = (
            f"Rule {rule_id} already executed in causal chain "
            f"{correlation_id} (execution {prior.id}). Rules {rule_id} and "
            f"{counterpart} form a cycle: each one's actions emit an event "
            "that triggers the other. Break the loop by narrowing one rule's "
            "conditions or removing the field mutation that re-triggers it."
        )
    else:
        reason = (
            f"Rule {rule_id} already executed in causal chain "
            f"{correlation_id} (execution {prior.id}). The rule's own actions "
            "emit an event that re-triggers it."
        )

    suppression = Suppression(
        status=AutomationExecutionStatus.SUPPRESSED_CYCLE,
        reason=reason,
        prior_execution_id=prior.id,
        counterpart_rule_id=counterpart,
        depth=depth,
    )

    logger.warning(
        "automation.suppressed_cycle",
        extra={
            "rule_id": str(rule_id),
            "counterpart_rule_id": str(counterpart) if counterpart else None,
            "correlation_id": str(correlation_id),
            "prior_execution_id": str(prior.id),
            "depth": depth,
        },
    )
    return suppression


def would_cycle(
    db: Session, *, rule_id: uuid.UUID, correlation_id: uuid.UUID
) -> Optional[uuid.UUID]:
    """The prior execution id of this rule in this chain, if any."""
    prior = prior_execution_in_chain(
        db, rule_id=rule_id, correlation_id=correlation_id
    )
    return prior.id if prior else None


__all__ = [
    "OCCUPYING_STATUSES",
    "Suppression",
    "check",
    "prior_execution_in_chain",
    "rule_of_causing_execution",
    "would_cycle",
]
