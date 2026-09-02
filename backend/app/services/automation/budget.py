"""ARCH-13 Step 13.3 — A6: the per-execution LLM budget."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.principal import Principal
from app.models.automation_execution import AutomationExecution
from app.services import llm_metering

logger = logging.getLogger("app.services.automation.budget")

_BUDGET_CONSTRAINT = "ck_automation_executions_spend_within_budget"


class BudgetExhausted(RuntimeError):
    """The execution's A6 ceiling would be breached."""

    def __init__(
        self,
        *,
        execution_id: uuid.UUID,
        rule_id: uuid.UUID,
        budget_micros: int,
        spent_micros: int,
        requested_micros: int,
        node_key: Optional[str] = None,
    ) -> None:
        self.execution_id = execution_id
        self.rule_id = rule_id
        self.budget_micros = budget_micros
        self.spent_micros = spent_micros
        self.requested_micros = requested_micros
        self.node_key = node_key
        super().__init__(
            f"Execution {execution_id} (rule {rule_id}) has "
            f"{max(0, budget_micros - spent_micros)} of "
            f"{budget_micros} budget micros remaining; node "
            f"{node_key or '?'} needs {requested_micros}. Raise the rule's "
            "budget_cost_micros or reduce the graph's LLM calls."
        )

    def as_details(self) -> dict[str, Any]:
        return {
            "execution_id": str(self.execution_id),
            "rule_id": str(self.rule_id),
            "node_key": self.node_key,
            "budget_cost_micros": self.budget_micros,
            "spent_cost_micros": self.spent_micros,
            "requested_cost_micros": self.requested_micros,
            "remaining_cost_micros": max(
                0, self.budget_micros - self.spent_micros
            ),
        }


@dataclass(frozen=True)
class BudgetedReservation:
    reservation: llm_metering.LLMReservation
    execution_id: uuid.UUID
    node_key: str

    @property
    def scope(self) -> str:
        return self.reservation.scope


def resolve_budget_micros(rule: Any) -> int:
    override = getattr(rule, "budget_cost_micros", None)
    if override is not None and int(override) >= 0:
        return int(override)
    return int(settings.AUTOMATION_DEFAULT_BUDGET_MICROS)


def estimate_cost_micros(
    reservation: llm_metering.LLMReservation,
) -> int:
    if reservation.input_price is None or reservation.output_price is None:
        return 0
    return int(
        reservation.input_price.cost_micros(reservation.estimated_input_tokens)
        + reservation.output_price.cost_micros(reservation.max_output_tokens)
    )


def _would_breach(execution: AutomationExecution, cost_micros: int) -> bool:
    return (
        int(execution.spent_cost_micros) + int(cost_micros)
        > int(execution.budget_cost_micros)
    )


def system_principal(
    *, execution: AutomationExecution, rule: Any
) -> Principal:
    created_by = getattr(rule, "created_by_user_id", None)
    extra_payload: dict[str, Any] = {
        "rule_id": str(execution.rule_id),
        "execution_id": str(execution.id),
        "correlation_id": str(execution.correlation_id),
        "created_by_user_id": str(created_by) if created_by is not None else None,
    }
    try:
        return Principal.for_system(
            job_name="jobs.automation.execute",
            **extra_payload,
        )
    except TypeError:
        return Principal.for_system(
            job_name="jobs.automation.execute",
            extra=extra_payload,
        )


def reserve_for_automation(
    db: Session,
    *,
    execution: AutomationExecution,
    node_key: str,
    prompt: str,
    ai_settings: Any,
    rule: Optional[Any] = None,
) -> BudgetedReservation:
    reservation = llm_metering.reserve_for_node(
        db,
        organization_id=execution.organization_id,
        workspace_id=execution.workspace_id,
        work_item_id=execution.work_item_id,
        scope=execution.scope_for(node_key),
        prompt=prompt,
        ai_settings=ai_settings,
        details_extra={
            "operation": "automation",
            "rule_id": str(execution.rule_id),
            "execution_id": str(execution.id),
            "node_key": node_key,
        },
    )

    worst_case = estimate_cost_micros(reservation)
    if _would_breach(execution, worst_case):
        raise BudgetExhausted(
            execution_id=execution.id,
            rule_id=execution.rule_id,
            budget_micros=int(execution.budget_cost_micros),
            spent_micros=int(execution.spent_cost_micros),
            requested_micros=worst_case,
            node_key=node_key,
        )

    logger.info(
        "automation.budget_reserved",
        extra={
            "execution_id": str(execution.id),
            "rule_id": str(execution.rule_id),
            "node_key": node_key,
            "worst_case_cost_micros": worst_case,
            "remaining_cost_micros": execution.remaining_budget_micros,
        },
    )
    return BudgetedReservation(
        reservation=reservation, execution_id=execution.id, node_key=node_key
    )


def settle_and_debit(
    db: Session,
    *,
    execution: AutomationExecution,
    budgeted: BudgetedReservation,
    token_usage: Any,
    estimated: bool = False,
) -> tuple[dict[str, Any], int]:
    summary = llm_metering.settle(
        db,
        reservation=budgeted.reservation,
        token_usage=token_usage,
        estimated=estimated,
    )
    actual = int(summary.get("total_cost_micros") or 0)

    if actual <= 0:
        return summary, 0

    savepoint = db.begin_nested()
    try:
        result = db.execute(
            update(AutomationExecution)
            .where(
                AutomationExecution.id == execution.id,
                AutomationExecution.spent_cost_micros
                + actual
                <= AutomationExecution.budget_cost_micros,
            )
            .values(
                spent_cost_micros=AutomationExecution.spent_cost_micros + actual
            )
            .returning(AutomationExecution.spent_cost_micros)
        ).scalar_one_or_none()
        savepoint.commit()
    except IntegrityError as exc:
        savepoint.rollback()
        if _BUDGET_CONSTRAINT not in str(getattr(exc, "orig", exc)):
            raise
        result = None

    if result is None:
        logger.warning(
            "automation.budget_exhausted",
            extra={
                "execution_id": str(execution.id),
                "rule_id": str(execution.rule_id),
                "node_key": budgeted.node_key,
                "actual_cost_micros": actual,
                "budget_cost_micros": int(execution.budget_cost_micros),
                "spent_cost_micros": int(execution.spent_cost_micros),
            },
        )
        raise BudgetExhausted(
            execution_id=execution.id,
            rule_id=execution.rule_id,
            budget_micros=int(execution.budget_cost_micros),
            spent_micros=int(execution.spent_cost_micros),
            requested_micros=actual,
            node_key=budgeted.node_key,
        )

    execution.spent_cost_micros = int(result)
    logger.info(
        "automation.budget_debited",
        extra={
            "execution_id": str(execution.id),
            "node_key": budgeted.node_key,
            "cost_micros": actual,
            "spent_cost_micros": int(result),
            "budget_cost_micros": int(execution.budget_cost_micros),
        },
    )
    return summary, actual


__all__ = [
    "BudgetExhausted",
    "BudgetedReservation",
    "estimate_cost_micros",
    "reserve_for_automation",
    "resolve_budget_micros",
    "settle_and_debit",
    "system_principal",
]
