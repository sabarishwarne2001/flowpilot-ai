"""Automation Rules API router endpoints for FlowPilot AI."""

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.models.automation import AutomationRule
from app.models.work_item import WorkItem
from app.models.automation_execution import (
    AutomationExecution,
    AutomationExecutionStatus,
    SUPPRESSED_STATUSES,
)
from app.services.automation_service import automation_service
from app.schemas.automation import (
    AutomationRuleCreate,
    AutomationRuleUpdate,
    AutomationRuleResponse,
    AutomationLogResponse,
    AutomationRuleTestRequest,
    AutomationRuleTestResponse,
)

router = APIRouter(tags=["Automation"])
logger = logging.getLogger("app.api.v1.automation")


class AutomationNodeRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    node_key: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    attempt: int = 0
    error: Optional[str] = None


class AutomationExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    rule_id: uuid.UUID
    rule_name: Optional[str] = None
    work_item_id: Optional[uuid.UUID] = None
    outbox_event_id: Optional[uuid.UUID] = None
    correlation_id: uuid.UUID
    depth: int
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    deadline_at: Optional[datetime] = None
    created_at: datetime
    budget_cost_micros: int
    spent_cost_micros: int
    node_count: int
    nodes_executed: int
    actions_executed: int
    emitted_event_ids: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)
    is_suppressed: bool = False
    duration_ms: Optional[int] = None


class AutomationExecutionPage(BaseModel):
    items: list[AutomationExecutionResponse]
    limit: int
    has_more: bool
    next_offset: Optional[int] = None


def _execution_view(row: AutomationExecution) -> AutomationExecutionResponse:
    duration_ms: Optional[int] = None
    if row.started_at is not None and row.completed_at is not None:
        delta = row.completed_at - row.started_at
        duration_ms = max(int(delta.total_seconds() * 1000), 0)

    status_value = row.status.value if hasattr(row.status, "value") else str(row.status)
    rule_name = getattr(row.rule, "name", None) if getattr(row, "rule", None) else None

    return AutomationExecutionResponse(
        id=row.id,
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        rule_id=row.rule_id,
        rule_name=rule_name,
        work_item_id=row.work_item_id,
        outbox_event_id=row.outbox_event_id,
        correlation_id=row.correlation_id,
        depth=row.depth,
        status=status_value,
        started_at=row.started_at,
        completed_at=row.completed_at,
        deadline_at=row.deadline_at,
        created_at=row.created_at,
        budget_cost_micros=row.budget_cost_micros,
        spent_cost_micros=row.spent_cost_micros,
        node_count=row.node_count,
        nodes_executed=row.nodes_executed,
        actions_executed=row.actions_executed,
        emitted_event_ids=list(row.emitted_event_ids or []),
        error=row.error,
        details=dict(row.details or {}),
        is_suppressed=status_value in {s.value for s in SUPPRESSED_STATUSES},
        duration_ms=duration_ms,
    )


@router.get("/executions", response_model=AutomationExecutionPage)
async def list_executions(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
    correlation_id: Optional[uuid.UUID] = Query(None),
    rule_id: Optional[uuid.UUID] = Query(None),
    work_item_id: Optional[uuid.UUID] = Query(None),
    execution_status: Optional[AutomationExecutionStatus] = Query(None, alias="status"),
    suppressed_only: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
) -> AutomationExecutionPage:
    stmt = select(AutomationExecution).where(
        AutomationExecution.workspace_id == context.workspace_id,
    )
    if correlation_id is not None:
        stmt = stmt.where(AutomationExecution.correlation_id == correlation_id)
    if rule_id is not None:
        stmt = stmt.where(AutomationExecution.rule_id == rule_id)
    if work_item_id is not None:
        stmt = stmt.where(AutomationExecution.work_item_id == work_item_id)
    if execution_status is not None:
        stmt = stmt.where(AutomationExecution.status == execution_status)
    if suppressed_only:
        stmt = stmt.where(AutomationExecution.status.in_(SUPPRESSED_STATUSES))

    if correlation_id is not None:
        stmt = stmt.order_by(
            AutomationExecution.depth.asc(),
            AutomationExecution.created_at.asc(),
            AutomationExecution.id.asc(),
        )
    else:
        stmt = stmt.order_by(
            AutomationExecution.created_at.desc(),
            AutomationExecution.id.desc(),
        )

    rows = db.execute(stmt.offset(offset).limit(limit + 1)).scalars().all()
    has_more = len(rows) > limit
    page = rows[:limit]

    return AutomationExecutionPage(
        items=[_execution_view(row) for row in page],
        limit=limit,
        has_more=has_more,
        next_offset=(offset + limit) if has_more else None,
    )


@router.get("/executions/{execution_id}", response_model=AutomationExecutionResponse)
async def get_execution(
    execution_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> Any:
    row = db.execute(
        select(AutomationExecution).where(
            AutomationExecution.id == execution_id,
            AutomationExecution.workspace_id == context.workspace_id,
        )
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found or you do not have permission to access it.",
        )
    return _execution_view(row)


@router.post("/rules", response_model=AutomationRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    rule_in: AutomationRuleCreate,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin)
) -> Any:
    rule = crud.create_automation_rule(
        db,
        workspace_id=context.workspace_id,
        obj_in=rule_in,
        created_by_user_id=context.user_id,
    )
    return rule


@router.get("/rules", response_model=list[AutomationRuleResponse])
async def list_rules(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100)
) -> Any:
    return crud.list_automation_rules(db, workspace_id=context.workspace_id, skip=skip, limit=limit)


_SUCCESS_STATUSES = frozenset({AutomationExecutionStatus.COMPLETED, AutomationExecutionStatus.SUCCEEDED if hasattr(AutomationExecutionStatus, "SUCCEEDED") else AutomationExecutionStatus.COMPLETED})


def _legacy_status(status: AutomationExecutionStatus) -> str:
    status_str = status.value if hasattr(status, "value") else str(status)
    return "SUCCESS" if status in _SUCCESS_STATUSES or status_str in ("COMPLETED", "SUCCEEDED") else "FAILED"


def _duration_ms(execution: AutomationExecution) -> Optional[int]:
    if execution.started_at is None or execution.completed_at is None:
        return None
    delta = execution.completed_at - execution.started_at
    return max(0, int(delta.total_seconds() * 1000))


def _action_summary(execution: AutomationExecution) -> str:
    actions = int(execution.actions_executed or 0)
    if actions == 0:
        return "no actions"
    if actions == 1:
        return "1 action"
    return f"{actions} actions"


@router.get("/logs", response_model=list[AutomationLogResponse])
async def list_rule_logs(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
) -> list[AutomationLogResponse]:
    rows = db.execute(
        select(AutomationExecution, AutomationRule.name, WorkItem.original_filename)
        .join(AutomationRule, AutomationExecution.rule_id == AutomationRule.id)
        .join(WorkItem, AutomationExecution.work_item_id == WorkItem.id)
        .where(AutomationExecution.workspace_id == context.workspace_id)
        .order_by(AutomationExecution.created_at.desc())
        .offset(skip)
        .limit(limit)
    ).all()

    response: list[AutomationLogResponse] = []
    for execution, rule_name, filename in rows:
        response.append(
            AutomationLogResponse(
                id=execution.id,
                rule_id=execution.rule_id,
                work_item_id=execution.work_item_id,
                rule_name=rule_name,
                document_name=filename,
                action_type=_action_summary(execution),
                status=_legacy_status(execution.status),
                log_message=execution.error,
                execution_status=execution.status.value if hasattr(execution.status, "value") else str(execution.status),
                execution_time_ms=_duration_ms(execution),
                spent_cost_micros=execution.spent_cost_micros,
                nodes_executed=execution.nodes_executed,
                actions_executed=execution.actions_executed,
                created_at=execution.created_at,
                updated_at=execution.updated_at if execution.updated_at else execution.created_at,
            )
        )
    return response


@router.get("/rules/{rule_id}", response_model=AutomationRuleResponse)
async def get_rule(
    rule_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor)
) -> Any:
    rule = crud.get_rule_by_id(db, workspace_id=context.workspace_id, rule_id=rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return rule


@router.patch("/rules/{rule_id}", response_model=AutomationRuleResponse)
async def update_rule(
    rule_id: uuid.UUID,
    rule_in: AutomationRuleUpdate,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin)
) -> Any:
    rule = crud.get_rule_by_id(db, workspace_id=context.workspace_id, rule_id=rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return crud.update_automation_rule(db, db_obj=rule, obj_in=rule_in)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin)
) -> Response:
    rule = crud.get_rule_by_id(db, workspace_id=context.workspace_id, rule_id=rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    crud.delete_automation_rule(db, db_obj=rule)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/rules/{rule_id}/test", response_model=AutomationRuleTestResponse)
async def test_rule(
    rule_id: uuid.UUID,
    payload: AutomationRuleTestRequest,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin)
) -> Any:
    rule = crud.get_rule_by_id(db, workspace_id=context.workspace_id, rule_id=rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    work_item = crud.get_work_item(db, workspace_id=context.workspace_id, work_item_id=payload.work_item_id)
    if work_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work item not found")

    return await automation_service.test_rule_for_work_item(db, rule=rule, work_item=work_item)
