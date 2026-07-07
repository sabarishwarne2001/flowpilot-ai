"""
Automation Rules API router endpoints for FlowPilot AI.

Exposes CRUD endpoints for managing trigger-action automation rules, 
enforcing strict multi-tenant boundary checks on every transaction.
"""

import logging
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, Response
from sqlalchemy.orm import Session
from app import crud
from app.api import deps
from app.models.user import User
from app.schemas.automation import AutomationRuleCreate, AutomationRuleUpdate, AutomationRuleResponse

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
    current_user: User = Depends(deps.get_current_active_user)
) -> Any:
    """
    Registers a new trigger-action automation rule within FlowPilot AI.
    
    Binds the rule strictly to the authenticated user account context.
    """
    rule = crud.create_automation_rule(db, obj_in=rule_in, user_id=current_user.id)
    logger.info(f"User {current_user.id} created Automation Rule '{rule.name}' [ID: {rule.id}]")
    return rule


@router.get(
    "", 
    response_model=list[AutomationRuleResponse],
    summary="List all Automation Rules",
    response_description="A paginated list of active and inactive Automation Rules owned by the user."
)
async def list_rules(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    skip: int = Query(0, ge=0, description="The number of rules to skip for pagination."),
    limit: int = Query(100, ge=1, le=100, description="The maximum number of rules to return.")
) -> Any:
    """
    Retrieves a paginated list of all Automation Rules configured by the authenticated user.
    """
    rules = crud.get_rules_by_user(db, user_id=current_user.id, skip=skip, limit=limit)
    return rules


@router.get(
    "/{rule_id}", 
    response_model=AutomationRuleResponse,
    summary="Get an Automation Rule by ID",
    response_description="The details of the requested Automation Rule."
)
async def get_rule(
    rule_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user)
) -> Any:
    """
    Retrieves detailed parameters for a specific Automation Rule.
    
    Enforces user isolation; queries for non-owned rules return a 404 error.
    """
    rule = crud.get_rule_by_id(db, rule_id=rule_id, user_id=current_user.id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Automation rule not found."
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
    current_user: User = Depends(deps.get_current_active_user)
) -> Any:
    """
    Performs partial, incremental modifications on a specific Automation Rule.
    
    Enforces user isolation; updates to non-owned rules return a 404 error.
    """
    rule = crud.get_rule_by_id(db, rule_id=rule_id, user_id=current_user.id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Automation rule not found."
        )
    
    updated_rule = crud.update_automation_rule(db, db_obj=rule, obj_in=rule_in)
    logger.info(f"User {current_user.id} updated Automation Rule [ID: {rule_id}]")
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
    current_user: User = Depends(deps.get_current_active_user)
) -> Response:
    """
    Removes a specific Automation Rule, triggering cascades on child execution logs.
    
    Enforces user isolation; deletions of non-owned rules return a 404 error.
    """
    rule = crud.get_rule_by_id(db, rule_id=rule_id, user_id=current_user.id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Automation rule not found."
        )
    
    crud.delete_automation_rule(db, db_obj=rule)
    logger.info(f"User {current_user.id} deleted Automation Rule [ID: {rule_id}]")
    return Response(status_code=status.HTTP_204_NO_CONTENT)