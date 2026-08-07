import uuid
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.automation import AutomationRule, AutomationLog
from app.models.work_item import WorkItem
from app.schemas.automation import AutomationRuleCreate, AutomationRuleUpdate

def create_automation_rule(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    created_by_user_id: uuid.UUID,
    obj_in: AutomationRuleCreate,
) -> AutomationRule:
    db_obj = AutomationRule(
        name=obj_in.name,
        priority=obj_in.priority,
        event=obj_in.event,
        conditions=[cond.model_dump() for cond in obj_in.conditions],
        logic_operator=obj_in.logic_operator,
        actions=[act.model_dump() for act in obj_in.actions],
        is_active=obj_in.is_active,
        workspace_id=workspace_id,
        created_by_user_id=created_by_user_id,
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def get_rule_by_id(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    rule_id: uuid.UUID,
) -> AutomationRule | None:
    statement = select(AutomationRule).where(
        AutomationRule.id == rule_id,
        AutomationRule.workspace_id == workspace_id,
    )
    return db.execute(statement).scalar_one_or_none()

def list_automation_rules(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    skip: int = 0,
    limit: int = 100,
) -> list[AutomationRule]:
    statement = (
        select(AutomationRule)
        .where(AutomationRule.workspace_id == workspace_id)
        .order_by(AutomationRule.priority.asc(), AutomationRule.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(db.execute(statement).scalars().all())

def list_active_rules_for_event(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    event: str,
) -> list[AutomationRule]:
    statement = (
        select(AutomationRule)
        .where(
            AutomationRule.workspace_id == workspace_id,
            AutomationRule.event == event,
            AutomationRule.is_active.is_(True),
        )
        .order_by(AutomationRule.priority.asc(), AutomationRule.created_at.desc())
    )
    return list(db.execute(statement).scalars().all())

def update_automation_rule(
    db: Session,
    *,
    db_obj: AutomationRule,
    obj_in: AutomationRuleUpdate,
) -> AutomationRule:
    update_data = obj_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "conditions" and value is not None:
            value = [
                cond.model_dump() if hasattr(cond, "model_dump") else cond
                for cond in value
            ]
        elif field == "actions" and value is not None:
            value = [
                act.model_dump() if hasattr(act, "model_dump") else act
                for act in value
            ]
        setattr(db_obj, field, value)
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def delete_automation_rule(
    db: Session,
    *,
    db_obj: AutomationRule,
) -> bool:
    db.delete(db_obj)
    db.commit()
    return True

def create_automation_log(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    rule_id: uuid.UUID,
    work_item_id: uuid.UUID,
    status: str,
    log_message: str | None = None,
) -> AutomationLog:
    db_obj = AutomationLog(
        workspace_id=workspace_id,
        rule_id=rule_id,
        work_item_id=work_item_id,
        status=status,
        log_message=log_message,
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def get_logs_by_rule(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    rule_id: uuid.UUID,
    skip: int = 0,
    limit: int = 100,
) -> list[AutomationLog]:
    statement = (
        select(AutomationLog)
        .where(
            AutomationLog.workspace_id == workspace_id,
            AutomationLog.rule_id == rule_id,
        )
        .order_by(AutomationLog.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(db.execute(statement).scalars().all())

def list_automation_logs(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    skip: int = 0,
    limit: int = 100,
) -> list[AutomationLog]:
    statement = (
        select(AutomationLog)
        .join(AutomationRule, AutomationLog.rule_id == AutomationRule.id)
        .join(WorkItem, AutomationLog.work_item_id == WorkItem.id)
        .where(AutomationLog.workspace_id == workspace_id)
        .order_by(AutomationLog.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    logs = list(db.execute(statement).scalars().all())
    for log in logs:
        log.rule_name = log.rule.name
        log.document_name = log.work_item.original_filename
        log.action_type = (
            log.rule.actions[0].get("action_type", "UNKNOWN")
            if log.rule.actions
            else "UNKNOWN"
        )
    return logs