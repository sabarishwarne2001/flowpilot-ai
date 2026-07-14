"""
Automation Rules Evaluation and Matching Service for FlowPilot AI.

Evaluates user-defined automation rules after document processing completes,
dispatches notifications, creates audit logs, and records execution statistics.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud
from app.models.work_item import WorkItem
from app.models.email_settings import EmailSettings
from app.services.notification.dispatcher import notification_dispatcher

from app.schemas.notification import NotificationCreate
from app.models.notification import (
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
    NotificationType,
)

logger = logging.getLogger("app.services.automation_service")


def _evaluate_condition(
    actual: Any,
    operator: str,
    target_value: str,
) -> bool:
    """
    Evaluate a rule condition against an actual document value.
    """

    operator = operator.upper().strip()

    if actual is None:
        return operator == "NOT_EQUALS"

    actual_str = str(actual).strip()
    target_str = target_value.strip()

    if operator == "EQUALS":
        return actual_str.lower() == target_str.lower()

    if operator == "NOT_EQUALS":
        return actual_str.lower() != target_str.lower()

    if operator == "CONTAINS":
        return target_str.lower() in actual_str.lower()

    try:
        actual_num = float(actual)
        target_num = float(target_str)
    except (TypeError, ValueError):
        logger.warning(
            "Numeric comparison failed. Actual='%s' Target='%s'",
            actual,
            target_str,
        )
        return False

    if operator == "GREATER_THAN":
        return actual_num > target_num

    if operator == "LESS_THAN":
        return actual_num < target_num

    if operator == "GREATER_THAN_OR_EQUAL":
        return actual_num >= target_num

    if operator == "LESS_THAN_OR_EQUAL":
        return actual_num <= target_num

    logger.warning("Unsupported automation operator '%s'.", operator)
    return False


def _get_nested_value(
    data: dict[str, Any],
    field_path: str,
) -> Any:
    """
    Resolve nested dictionary values using dot notation.

    Examples:
        name
        classification_details.document_classification
        invoice.vendor.address.city
    """

    current: Any = data

    for key in field_path.split("."):

        if not isinstance(current, dict):
            return None

        current = current.get(key)

        if current is None:
            return None

    return current


class AutomationService:
    """
    Executes user automation rules for completed work items.
    """

    async def execute_rules_for_work_item(
        self,
        db: Session,
        *,
        work_item_id: uuid.UUID,
        event: str,
    ) -> dict[str, int]:

        stats = {
            "evaluated": 0,
            "matched": 0,
            "succeeded": 0,
            "failed": 0,
        }

        logger.info(
            "Executing automation rules for WorkItem %s (%s).",
            work_item_id,
            event,
        )

        work_item = db.execute(
            select(WorkItem).where(
                WorkItem.id == work_item_id
            )
        ).scalar_one_or_none()

        if work_item is None:
            logger.error("WorkItem %s not found.", work_item_id)
            return stats

        rules = crud.get_active_rules_by_user_and_event(
            db,
            user_id=work_item.user_id,
            event=event,
        )

        email_settings = crud.get_email_settings(
            db,
            user_id=work_item.user_id,
        )

        if email_settings is None:

            logger.warning(
                "No Email Settings configured for user %s.",
                work_item.user_id,
            )

            return stats

        if not email_settings.is_enabled:

            logger.info(
                "Email notifications are disabled for user %s.",
                work_item.user_id,
            )

            return stats
        

        for rule in rules:

            stats["evaluated"] += 1

            try:

                if hasattr(work_item, rule.field):
                    actual_value = getattr(work_item, rule.field)

                else:
                    actual_value = _get_nested_value(
                        work_item.extracted_entities or {},
                        rule.field,
                    )

                if not _evaluate_condition(
                    actual_value,
                    rule.operator,
                    rule.value,
                ):
                    continue

                stats["matched"] += 1

                recipient = (
                    rule.action_config.get("recipient", "").strip()
                )

                title = f"Automation Rule Triggered: {rule.name}"

                body = (
                    f"Document: {work_item.original_filename}\n"
                    f"Rule: {rule.name}\n"
                    f"Status: Matched\n\n"
                    f"{work_item.summary or 'No summary available.'}"
                )

                success = await notification_dispatcher.send(
                    action_type=rule.action_type,
                    settings=email_settings,
                    recipient=recipient,
                    title=title,
                    body=body,
                )

                if not success:
                    raise RuntimeError(
                        "Notification provider reported failure."
                    )

                crud.create_notification(
                    db,
                    notification_in=NotificationCreate(
                        user_id=work_item.user_id,
                        work_item_id=work_item.id,
                        title=title,
                        message=body,
                        notification_type=NotificationType.AUTOMATION,
                        priority=NotificationPriority.INFO,
                        delivery_channel=NotificationChannel.IN_APP,
                        delivery_status=NotificationStatus.SENT,
                    ),
                )

                crud.create_automation_log(
                    db,
                    rule_id=rule.id,
                    work_item_id=work_item.id,
                    status="SUCCESS",
                    log_message=(
                        f"{rule.action_type} -> {recipient}"
                    ),
                )

                stats["succeeded"] += 1

            except Exception as exc:

                logger.exception(
                    "Automation rule '%s' failed.",
                    rule.name,
                )

                stats["failed"] += 1

                db.rollback()

                try:

                    crud.create_automation_log(
                        db,
                        rule_id=rule.id,
                        work_item_id=work_item.id,
                        status="FAILED",
                        log_message=str(exc)[:5000],
                    )

                except Exception:
                    logger.exception(
                        "Unable to create automation audit log."
                    )

        logger.info(
            "Automation execution complete. %s",
            stats,
        )

        return stats


automation_service = AutomationService()