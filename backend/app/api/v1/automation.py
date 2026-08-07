"""
Automation Rules API router endpoints for FlowPilot AI.
"""

import logging
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, Response
from sqlalchemy.orm import Session
from app import crud
from app.api import deps
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


@router.post(
    "", 
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
    "", 
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
    "/{rule_id}", 
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
    "/{rule_id}", 
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
    "/{rule_id}", 
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
    "/{rule_id}/test",
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