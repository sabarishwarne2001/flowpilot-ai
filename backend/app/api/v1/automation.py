"""
Automation Rules API router endpoints for FlowPilot AI.
"""

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


# ==========================================================================
# ARCH-13 execution traces
# ==========================================================================


class AutomationNodeRunResponse(BaseModel):
    """One node inside an execution."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    node_key: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    attempt: int = 0
    error: Optional[str] = None


class AutomationExecutionResponse(BaseModel):
    """One execution, with the fields a causal timeline needs."""

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
    """A page of executions, newest first."""

    items: list[AutomationExecutionResponse]
    limit: int
    has_more: bool
    next_offset: Optional[int] = None


def _execution_view(row: AutomationExecution) -> AutomationExecutionResponse:
    duration_ms: Optional[int] = None
    if row.started_at is not None and row.completed_at is not None:
        delta = row.completed_at - row.started_at
        duration_ms = max(int(delta.total_seconds() * 1000), 0)

    status_value = (
        row.status.value if hasattr(row.status, "value") else str(row.status)
    )

    rule_name: Optional[str] = None
    rule = getattr(row, "rule", None)
    if rule is not None:
        rule_name = getattr(rule, "name", None)

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
        is_suppressed=status_value
        in {s.value for s in SUPPRESSED_STATUSES},
        duration_ms=duration_ms,
    )


@router.get(
    "/executions",
    response_model=AutomationExecutionPage,
    summary="List Automation Executions (causal traces)",
    response_description=(
        "Executions newest first, including SUPPRESSED_CYCLE and "
        "SUPPRESSED_DEPTH refusals that never appear in /logs."
    ),
)
async def list_executions(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
    correlation_id: Optional[uuid.UUID] = Query(
        None,
        description=(
            "Return one causal chain. This is the filter that makes a cycle "
            "visible: a loop is a property of a chain, not of a rule."
        ),
    ),
    rule_id: Optional[uuid.UUID] = Query(
        None, description="Return executions of one rule."
    ),
    work_item_id: Optional[uuid.UUID] = Query(
        None, description="Return executions triggered by one document."
    ),
    execution_status: Optional[AutomationExecutionStatus] = Query(
        None, alias="status", description="Filter by execution status."
    ),
    suppressed_only: bool = Query(
        False,
        description=(
            "Only refusals — SUPPRESSED_CYCLE and SUPPRESSED_DEPTH. The "
            "'what is silently not running' view."
        ),
    ),
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


@router.get(
    "/executions/{execution_id}",
    response_model=AutomationExecutionResponse,
    summary="Get one Automation Execution",
    response_description="A single execution including its suppression detail.",
)
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


@router.post(
    "/rules", 
    response_model=AutomationRuleResponse, 
    status_code=status.HTTP_201_CREATED,
    summary="Create a new Automation Rule",
    response_description="The registered Automation Rule with generated UUID."
)
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
    logger.info(f"User {context.user_id} created Automation Rule '{rule.name}' [ID: {rule.id}] in workspace {context.workspace_id}")
    return rule


@router.get(
    "/rules", 
    response_model=list[AutomationRuleResponse],
    summary="List all Automation Rules",
    response_description="A paginated list of active and inactive Automation Rules."
)
async def list_rules(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
    skip: int = Query(0, ge=0, description="The number of rules to skip for pagination."),
    limit: int = Query(100, ge=1, le=100, description="The maximum number of rules to return.")
) -> Any:
    rules = crud.list_automation_rules(db, workspace_id=context.workspace_id, skip=skip, limit=limit)
    return rules


@router.get(
    "/logs",
    response_model=list[AutomationLogResponse],
    summary="List Automation Execution Logs",
    response_description="Execution history for all automation rules.",
)
async def list_rule_logs(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
) -> list[AutomationLogResponse]:
    logs = crud.list_automation_logs(
        db,
        workspace_id=context.workspace_id,
        skip=skip,
        limit=limit,
    )

    response: list[AutomationLogResponse] = []

    for log in logs:
        response.append(
            AutomationLogResponse(
                id=log.id,
                rule_id=log.rule_id,
                work_item_id=log.work_item_id,
                rule_name=log.rule_name,
                document_name=log.document_name,
                action_type=log.action_type,
                status=log.status,
                log_message=log.log_message,
                created_at=log.created_at,
                updated_at=log.updated_at,
            )
        )

    logger.info(
        "Returned %d automation logs for workspace %s.",
        len(response),
        context.workspace_id,
    )

    return response


@router.get(
    "/rules/{rule_id}", 
    response_model=AutomationRuleResponse,
    summary="Get an Automation Rule by ID",
    response_description="The details of the requested Automation Rule."
)
async def get_rule(
    rule_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor)
) -> Any:
    rule = crud.get_rule_by_id(db, workspace_id=context.workspace_id, rule_id=rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Automation rule not found or you do not have permission to access it."
        )
    return rule


@router.patch(
    "/rules/{rule_id}", 
    response_model=AutomationRuleResponse,
    summary="Update an Automation Rule",
    response_description="The updated Automation Rule."
)
async def update_rule(
    rule_id: uuid.UUID,
    rule_in: AutomationRuleUpdate,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin)
) -> Any:
    rule = crud.get_rule_by_id(db, workspace_id=context.workspace_id, rule_id=rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Automation rule not found or you do not have permission to access it."
        )
    
    updated_rule = crud.update_automation_rule(db, db_obj=rule, obj_in=rule_in)
    logger.info(f"User {context.user_id} updated Automation Rule [ID: {rule_id}] inside workspace {context.workspace_id}")
    return updated_rule


@router.delete(
    "/rules/{rule_id}", 
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an Automation Rule",
    response_description="Empty response indicating successful deletion."
)
async def delete_rule(
    rule_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin)
) -> Response:
    rule = crud.get_rule_by_id(db, workspace_id=context.workspace_id, rule_id=rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Automation rule not found or you do not have permission to access it."
        )
    
    crud.delete_automation_rule(db, db_obj=rule)
    logger.info(f"User {context.user_id} deleted Automation Rule [ID: {rule_id}] in workspace {context.workspace_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/rules/{rule_id}/test",
    response_model=AutomationRuleTestResponse,
    summary="Test an Automation Rule against a Work Item",
    response_description="Detailed results of the test match evaluation."
)
async def test_rule(
    rule_id: uuid.UUID,
    payload: AutomationRuleTestRequest,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin)
) -> Any:
    rule = crud.get_rule_by_id(db, workspace_id=context.workspace_id, rule_id=rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Automation rule not found or you do not have permission to access it."
        )

    work_item = crud.get_work_item(
        db,
        workspace_id=context.workspace_id,
        work_item_id=payload.work_item_id,
    )
    
    if work_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work item not found or you do not have permission to access it."
        )

    result = await automation_service.test_rule_for_work_item(
        db, rule=rule, work_item=work_item
    )
    return result