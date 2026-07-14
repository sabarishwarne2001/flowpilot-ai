"""
Database CRUD (Create, Read, Update, Delete) repository layer for Automation
Rules and Automation Logs.

Provides database access for automation rule management while enforcing
user ownership and keeping business logic outside the repository layer.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.automation import AutomationLog, AutomationRule
from app.models.work_item import WorkItem

from app.schemas.automation import AutomationRuleCreate, AutomationRuleUpdate


def create_automation_rule(
    db: Session,
    *,
    obj_in: AutomationRuleCreate,
    user_id: uuid.UUID,
) -> AutomationRule:
    """
    Create and persist a new automation rule.
    """
    db_obj = AutomationRule(
        name=obj_in.name,
        event=obj_in.event,
        field=obj_in.field,
        operator=obj_in.operator,
        value=obj_in.value,
        action_type=obj_in.action_type,
        action_config=obj_in.action_config,
        is_active=obj_in.is_active,
        user_id=user_id,
    )

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)

    return db_obj


def get_rule_by_id(
    db: Session,
    *,
    rule_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AutomationRule | None:
    """
    Retrieve a single automation rule owned by a user.
    """
    statement = select(AutomationRule).where(
        AutomationRule.id == rule_id,
        AutomationRule.user_id == user_id,
    )

    return db.execute(statement).scalar_one_or_none()


def get_rules_by_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    skip: int = 0,
    limit: int = 100,
) -> list[AutomationRule]:
    """
    Retrieve paginated automation rules for a user.
    """
    statement = (
        select(AutomationRule)
        .where(AutomationRule.user_id == user_id)
        .order_by(AutomationRule.created_at.desc())
        .offset(skip)
        .limit(limit)
    )

    return list(db.execute(statement).scalars().all())


def get_active_rules_by_user_and_event(
    db: Session,
    *,
    user_id: uuid.UUID,
    event: str,
) -> list[AutomationRule]:
    """
    Retrieve active automation rules for a specific event.
    """
    statement = (
        select(AutomationRule)
        .where(
            AutomationRule.user_id == user_id,
            AutomationRule.event == event,
            AutomationRule.is_active.is_(True),
        )
        .order_by(AutomationRule.created_at.desc())
    )

    return list(db.execute(statement).scalars().all())


def update_automation_rule(
    db: Session,
    *,
    db_obj: AutomationRule,
    obj_in: AutomationRuleUpdate,
) -> AutomationRule:
    """
    Apply a partial update to an automation rule.
    """
    update_data = obj_in.model_dump(exclude_unset=True)

    for field, value in update_data.items():
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
    """
    Delete an automation rule.

    Child execution logs are removed automatically by the database
    through ON DELETE CASCADE.
    """
    db.delete(db_obj)
    db.commit()

    return True


def create_automation_log(
    db: Session,
    *,
    rule_id: uuid.UUID,
    work_item_id: uuid.UUID,
    status: str,
    log_message: str | None = None,
) -> AutomationLog:
    """
    Create an automation execution log.
    """
    db_obj = AutomationLog(
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
    rule_id: uuid.UUID,
    user_id: uuid.UUID,
    skip: int = 0,
    limit: int = 100,
) -> list[AutomationLog]:
    """
    Retrieve paginated execution logs for an automation rule.

    User ownership is enforced through the parent AutomationRule.
    """
    statement = (
        select(AutomationLog)
        .join(AutomationRule, AutomationLog.rule_id == AutomationRule.id)
        .where(
            AutomationLog.rule_id == rule_id,
            AutomationRule.user_id == user_id,
        )
        .order_by(AutomationLog.created_at.desc())
        .offset(skip)
        .limit(limit)
    )


def get_logs_by_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    skip: int = 0,
    limit: int = 100,
) -> list[AutomationLog]:
    """
    Retrieve all automation execution logs belonging to a user.

    Enrich each log with rule metadata and document metadata
    required by the frontend audit timeline.
    """

    statement = (
        select(AutomationLog)
        .join(
            AutomationRule,
            AutomationLog.rule_id == AutomationRule.id,
        )
        .join(
            WorkItem,
            AutomationLog.work_item_id == WorkItem.id,
        )
        .where(
            AutomationRule.user_id == user_id,
        )
        .order_by(
            AutomationLog.created_at.desc(),
        )
        .offset(skip)
        .limit(limit)
    )

    logs = list(
        db.execute(statement).scalars().all()
    )

    #
    # Attach additional frontend fields.
    #
    for log in logs:
        log.rule_name = log.rule.name
        log.document_name = log.work_item.original_filename
        log.action_type = log.rule.action_type

    return logs