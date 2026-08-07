"""
Automation Rules Evaluation and Matching Service for FlowPilot AI.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud
from app.models.work_item import WorkItem
from app.models.email_settings import EmailSettings
from app.models.automation import AutomationRule
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
    operator = operator.upper().strip()
    target_value = target_value.strip() if target_value else ""

    if operator == "EXISTS":
        return actual is not None

    actual_is_empty = (
        actual is None
        or str(actual).strip() == ""
        or (isinstance(actual, (list, dict, set)) and len(actual) == 0)
    )
    if operator == "IS_EMPTY":
        return actual_is_empty
    if operator == "IS_NOT_EMPTY":
        return not actual_is_empty

    if actual is None:
        if operator in ("NOT_EQUALS", "NOT_CONTAINS", "NOT_IN"):
            return True
        return False

    actual_str = str(actual).strip()
    actual_lower = actual_str.lower()
    target_lower = target_value.lower()

    if operator in ("CONTAINS", "NOT_CONTAINS"):
        if isinstance(actual, (list, tuple, set)):
            actual_list = [str(x).strip().lower() for x in actual]
            is_contained = target_lower in actual_list
            return is_contained if operator == "CONTAINS" else not is_contained
        else:
            is_contained = target_lower in actual_lower
            return is_contained if operator == "CONTAINS" else not is_contained

    if operator == "EQUALS":
        return actual_lower == target_lower
    if operator == "NOT_EQUALS":
        return actual_lower != target_lower
    if operator == "STARTS_WITH":
        return actual_lower.startswith(target_lower)
    if operator == "ENDS_WITH":
        return actual_lower.endswith(target_lower)

    if operator in ("IN", "NOT_IN"):
        targets_list = [v.strip().lower() for v in target_value.split(",") if v.strip()]
        if isinstance(actual, (list, tuple, set)):
            actual_list = [str(x).strip().lower() for x in actual]
            has_intersection = any(x in targets_list for x in actual_list)
            return has_intersection if operator == "IN" else not has_intersection
        else:
            is_in = actual_lower in targets_list
            return is_in if operator == "IN" else not is_in

    if operator in ("ARRAY_CONTAINS_ANY", "ARRAY_CONTAINS_ALL"):
        targets = {v.strip().lower() for v in target_value.split(",") if v.strip()}
        if not targets:
            return False
        if not isinstance(actual, (list, tuple, set)):
            return False

        actual_set = {str(x).strip().lower() for x in actual}
        if operator == "ARRAY_CONTAINS_ANY":
            return bool(actual_set & targets)
        else:
            return targets.issubset(actual_set)

    if operator == "BETWEEN":
        normalized_range = target_value.replace("..", ",").replace(" - ", ",")
        if "," not in normalized_range and "-" in normalized_range:
            split_idx = normalized_range.find("-", 1)
            if split_idx != -1:
                normalized_range = normalized_range[:split_idx] + "," + normalized_range[split_idx+1:]
                
        parts = [p.strip() for p in normalized_range.split(",") if p.strip()]
        if len(parts) != 2:
            logger.warning("Malformed BETWEEN range values: '%s'.", target_value)
            return False
        try:
            low = float(parts[0])
            high = float(parts[1])
            val = float(actual)
            return low <= val <= high
        except (TypeError, ValueError):
            logger.warning("BETWEEN comparison failed. Value='%s' Range='%s'", actual, target_value)
            return False

    try:
        actual_num = float(actual)
        target_num = float(target_value)
    except (TypeError, ValueError):
        logger.warning(
            "Numeric comparison failed. Actual='%s' Target='%s'",
            actual,
            target_value,
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
    current: Any = data
    for key in field_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def _get_condition_attribute(condition: Any, attr: str) -> Any:
    if hasattr(condition, attr):
        return getattr(condition, attr)
    if isinstance(condition, dict):
        return condition.get(attr)
    return None


def _evaluate_rule_conditions(
    rule: Any,
    work_item: WorkItem,
) -> bool:
    conditions = getattr(rule, "conditions", []) or []
    if not conditions:
        return False

    logic_operator = getattr(rule, "logic_operator", "AND")
    if isinstance(logic_operator, str):
        logic_operator = logic_operator.upper().strip()

    if logic_operator not in ("AND", "OR"):
        logger.warning("Unknown logic operator '%s'.", logic_operator)
        logic_operator = "AND"

    matched_results = []
    for cond in conditions:
        field_path = _get_condition_attribute(cond, "field")
        operator = _get_condition_attribute(cond, "operator")
        target_value = _get_condition_attribute(cond, "value")

        if not field_path or not operator or target_value is None:
            matched_results.append(False)
            continue

        if hasattr(work_item, field_path):
            actual_value = getattr(work_item, field_path)
        else:
            actual_value = _get_nested_value(
                work_item.extracted_entities or {},
                field_path,
            )

        is_matched = _evaluate_condition(actual_value, operator, target_value)

        if logic_operator == "AND" and not is_matched:
            return False
        if logic_operator == "OR" and is_matched:
            return True

        matched_results.append(is_matched)

    if logic_operator == "OR":
        return any(matched_results)
    return all(matched_results)


class AutomationService:
    """
    Executes user automation rules for completed work items scoped to a workspace.
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
            select(WorkItem).where(WorkItem.id == work_item_id)
        ).scalar_one_or_none()

        if work_item is None:
            logger.error("WorkItem %s not found.", work_item_id)
            return stats

        workspace_id = work_item.workspace_id

        raw_rules = crud.list_active_rules_for_event(
            db,
            workspace_id=workspace_id,
            event=event,
        )

        rules = sorted(
            raw_rules,
            key=lambda r: (
                r.priority,
                r.created_at.timestamp() if getattr(r, "created_at", None) else 0
            )
        )

        email_settings = crud.get_email_settings(
            db,
            workspace_id=workspace_id,
        )

        if email_settings is None:
            logger.warning("No Email Settings configured for workspace %s.", workspace_id)
            return stats

        if not email_settings.is_enabled:
            logger.info("Email notifications are disabled for workspace %s.", workspace_id)
            return stats

        for rule in rules:
            stats["evaluated"] += 1
            try:
                if not _evaluate_rule_conditions(rule, work_item):
                    continue

                stats["matched"] += 1

                actions = getattr(rule, "actions", []) or []
                actions_succeeded = True
                action_logs = []

                for idx, action in enumerate(actions):
                    act_type = _get_condition_attribute(action, "action_type")
                    act_config = _get_condition_attribute(action, "config") or {}
                    
                    recipient = act_config.get("recipient", "").strip()
                    title = f"Automation Rule Triggered: {rule.name}"
                    body = (
                        f"Document: {work_item.original_filename}\n"
                        f"Rule: {rule.name}\n"
                        f"Status: Matched\n\n"
                        f"{work_item.summary or 'No summary available.'}"
                    )

                    success = await notification_dispatcher.send(
                        action_type=act_type,
                        settings=email_settings,
                        recipient=recipient,
                        title=title,
                        body=body,
                    )

                    if not success:
                        actions_succeeded = False
                        logger.error(
                            "Action #%d (%s) of rule '%s' failed to execute.",
                            idx + 1, act_type, rule.name
                        )
                    else:
                        action_logs.append(f"{act_type} -> {recipient}")

                if not actions_succeeded:
                    raise RuntimeError("One or more configured actions failed to execute successfully.")

                recipient_user = work_item.created_by
                if recipient_user is not None:
                    crud.create_notification(
                        db,
                        workspace_id=workspace_id,
                        notification_in=NotificationCreate(
                            user_id=recipient_user.id,
                            work_item_id=work_item.id,
                            title=f"Automation Rule Triggered: {rule.name}",
                            message="Conditions matched. Actions executed successfully.",
                            notification_type=NotificationType.AUTOMATION,
                            priority=NotificationPriority.INFO,
                            delivery_channel=NotificationChannel.IN_APP,
                            delivery_status=NotificationStatus.SENT,
                        ),
                    )

                crud.create_automation_log(
                    db,
                    workspace_id=workspace_id,
                    rule_id=rule.id,
                    work_item_id=work_item.id,
                    status="SUCCESS",
                    log_message=" | ".join(action_logs) or "All actions executed.",
                )

                stats["succeeded"] += 1

            except Exception as exc:
                logger.exception("Automation rule '%s' failed.", rule.name)
                stats["failed"] += 1
                db.rollback()

                try:
                    crud.create_automation_log(
                        db,
                        workspace_id=workspace_id,
                        rule_id=rule.id,
                        work_item_id=work_item.id,
                        status="FAILED",
                        log_message=str(exc)[:5000],
                    )
                except Exception:
                    logger.exception("Unable to create automation audit log.")

        logger.info("Automation execution complete. %s", stats)
        return stats

    async def test_rule_for_work_item(
        self,
        db: Session,
        *,
        rule: AutomationRule,
        work_item: WorkItem,
    ) -> dict[str, Any]:
        start_time = time.perf_counter()
        success = True
        matched = False
        notification_sent = False
        message = "Rule conditions were not satisfied."
        workspace_id = work_item.workspace_id

        try:
            if _evaluate_rule_conditions(rule, work_item):
                matched = True
                log_msg = "[MANUAL TEST RUN] Rule conditions matched."
                email_settings = crud.get_email_settings(db, workspace_id=workspace_id)

                if email_settings and email_settings.is_enabled:
                    actions = getattr(rule, "actions", []) or []
                    actions_succeeded = True
                    action_logs = []

                    for idx, action in enumerate(actions):
                        act_type = _get_condition_attribute(action, "action_type")
                        act_config = _get_condition_attribute(action, "config") or {}
                        recipient = act_config.get("recipient", "").strip()
                        title = f"[TEST MATCHED] Automation Rule: {rule.name}"
                        body = (
                            f"[MANUAL AUTOMATION TEST RUN]\n"
                            f"Document: {work_item.original_filename}\n"
                            f"Rule: {rule.name}\n"
                            f"Status: Matched\n\n"
                            f"{work_item.summary or 'No summary available.'}"
                        )

                        ok = await notification_dispatcher.send(
                            action_type=act_type,
                            settings=email_settings,
                            recipient=recipient,
                            title=title,
                            body=body,
                        )

                        if ok:
                            action_logs.append(f"{act_type} -> {recipient}")
                        else:
                            actions_succeeded = False
                            logger.error("Manual test action #%d failed.", idx + 1)
                            crud.create_automation_log(
                                db,
                                workspace_id=workspace_id,
                                rule_id=rule.id,
                                work_item_id=work_item.id,
                                status="FAILED",
                                log_message=f"[MANUAL TEST FAILED] Action #{idx + 1} ({act_type}) failed for {recipient}.",
                            )

                    if actions_succeeded:
                        notification_sent = True
                        message = f"Rule conditions met. Manual test actions executed: {', '.join(action_logs)}"
                        crud.create_automation_log(
                            db,
                            workspace_id=workspace_id,
                            rule_id=rule.id,
                            work_item_id=work_item.id,
                            status="SUCCESS",
                            log_message=f"{log_msg} Actions executed: {', '.join(action_logs)}",
                        )
                    else:
                        success = False
                        message = "Rule conditions met, but one or more actions failed to execute."
                else:
                    if not email_settings:
                        message = "Rule conditions met. Email not sent because Email settings are missing."
                    else:
                        message = "Rule conditions met. Email not sent because SMTP is disabled in settings."
                    
                    crud.create_automation_log(
                        db,
                        workspace_id=workspace_id,
                        rule_id=rule.id,
                        work_item_id=work_item.id,
                        status="SUCCESS",
                        log_message=f"{log_msg} {message}",
                    )

                db.commit()

        except Exception as exc:
            success = False
            message = f"Error evaluating manual rule conditions: {str(exc)}"
            try:
                crud.create_automation_log(
                    db,
                    workspace_id=workspace_id,
                    rule_id=rule.id,
                    work_item_id=work_item.id,
                    status="FAILED",
                    log_message=f"[MANUAL TEST FAILED] {str(exc)[:5000]}",
                )
                db.commit()
            except Exception:
                logger.exception("Unable to write manual automation test error log.")

        execution_time_ms = (time.perf_counter() - start_time) * 1000.0

        return {
            "success": success,
            "matched": matched,
            "notification_sent": notification_sent,
            "message": message,
            "execution_time_ms": round(execution_time_ms, 2),
        }


automation_service = AutomationService()