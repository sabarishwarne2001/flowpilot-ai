"""Automation Rules Evaluation and Matching Service for FlowPilot AI."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud
from app.models.automation import AutomationRule
from app.models.email_settings import EmailSettings
from app.models.notification import (
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
    NotificationType,
)
from app.models.work_item import WorkItem
from app.schemas.notification import NotificationCreate
from app.services.notification.dispatcher import notification_dispatcher

logger = logging.getLogger("app.services.automation_service")

VALUELESS_OPERATORS: frozenset[str] = frozenset({"EXISTS", "IS_EMPTY", "IS_NOT_EMPTY"})
EMAIL_ACTION_TYPES: frozenset[str] = frozenset({"email", "send_email"})


class ActionFailure(RuntimeError):
    def __init__(self, message: str, *, recoverable: bool = True) -> None:
        super().__init__(message)
        self.recoverable = recoverable


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
        return operator in ("NOT_EQUALS", "NOT_CONTAINS", "NOT_IN")

    actual_str = str(actual).strip()
    actual_lower = actual_str.lower()
    target_lower = target_value.lower()

    if operator in ("CONTAINS", "NOT_CONTAINS"):
        if isinstance(actual, (list, tuple, set)):
            actual_list = [str(x).strip().lower() for x in actual]
            is_contained = target_lower in actual_list
            return is_contained if operator == "CONTAINS" else not is_contained
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
        is_in = actual_lower in targets_list
        return is_in if operator == "IN" else not is_in

    if operator in ("ARRAY_CONTAINS_ANY", "ARRAY_CONTAINS_ALL"):
        targets = {v.strip().lower() for v in target_value.split(",") if v.strip()}
        if not targets or not isinstance(actual, (list, tuple, set)):
            return False
        actual_set = {str(x).strip().lower() for x in actual}
        return bool(actual_set & targets) if operator == "ARRAY_CONTAINS_ANY" else targets.issubset(actual_set)

    if operator == "BETWEEN":
        normalized_range = target_value.replace("..", ",").replace(" - ", ",")
        if "," not in normalized_range and "-" in normalized_range:
            split_idx = normalized_range.find("-", 1)
            if split_idx != -1:
                normalized_range = normalized_range[:split_idx] + "," + normalized_range[split_idx+1:]
        parts = [p.strip() for p in normalized_range.split(",") if p.strip()]
        if len(parts) != 2:
            return False
        try:
            low, high, val = float(parts[0]), float(parts[1]), float(actual)
            return low <= val <= high
        except (TypeError, ValueError):
            return False

    try:
        actual_num, target_num = float(actual), float(target_value)
    except (TypeError, ValueError):
        return False

    if operator == "GREATER_THAN":
        return actual_num > target_num
    if operator == "LESS_THAN":
        return actual_num < target_num
    if operator == "GREATER_THAN_OR_EQUAL":
        return actual_num >= target_num
    if operator == "LESS_THAN_OR_EQUAL":
        return actual_num <= target_num

    return False


def _get_nested_value(data: dict[str, Any], field_path: str) -> Any:
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


def resolve_field(work_item: WorkItem, field_path: str) -> Any:
    if hasattr(work_item, field_path):
        return getattr(work_item, field_path)
    entities = work_item.extracted_entities or {}
    if not isinstance(entities, dict):
        entities = {}
    if field_path.startswith("extracted_entities."):
        sub_path = field_path[len("extracted_entities."):]
        val = _get_nested_value(entities, sub_path)
        if val is not None:
            return val
    return _get_nested_value(entities, field_path)


def _evaluate_rule_conditions(rule: Any, work_item: WorkItem) -> bool:
    conditions = getattr(rule, "conditions", []) or []
    if not conditions:
        return False

    logic_operator = getattr(rule, "logic_operator", "AND")
    if isinstance(logic_operator, str):
        logic_operator = logic_operator.upper().strip()
    if logic_operator not in ("AND", "OR"):
        logic_operator = "AND"

    matched_results = []
    for cond in conditions:
        field_path = _get_condition_attribute(cond, "field")
        operator = _get_condition_attribute(cond, "operator")
        target_value = _get_condition_attribute(cond, "value")

        normalised_operator = operator.upper().strip() if isinstance(operator, str) else ""
        needs_value = normalised_operator not in VALUELESS_OPERATORS

        if not field_path or not operator or (needs_value and target_value is None):
            matched_results.append(False)
            if logic_operator == "AND":
                return False
            continue

        actual_value = resolve_field(work_item, field_path)
        is_matched = _evaluate_condition(actual_value, operator, target_value or "")

        if logic_operator == "AND" and not is_matched:
            return False
        if logic_operator == "OR" and is_matched:
            return True

        matched_results.append(is_matched)

    return any(matched_results) if logic_operator == "OR" else all(matched_results)


@dataclass
class _LazyEmailSettings:
    db: Session
    workspace_id: uuid.UUID
    _resolved: bool = field(default=False, init=False)
    _settings: Optional[EmailSettings] = field(default=None, init=False)
    _reason: Optional[str] = field(default=None, init=False)

    def _resolve(self) -> None:
        if self._resolved:
            return
        self._resolved = True
        resolved = crud.get_email_settings(self.db, workspace_id=self.workspace_id)
        if resolved is None:
            self._reason = "No email settings configured."
            return
        if not resolved.is_enabled:
            self._reason = "Email delivery is disabled."
            return
        self._settings = resolved

    def require(self) -> EmailSettings:
        self._resolve()
        if self._settings is None:
            raise ActionFailure(self._reason or "Email settings unavailable.")
        return self._settings

    @property
    def available(self) -> bool:
        self._resolve()
        return self._settings is not None

    @property
    def unavailable_reason(self) -> Optional[str]:
        self._resolve()
        return self._reason


def _render_action_message(*, rule: AutomationRule, work_item: WorkItem, prefix: str = "") -> tuple[str, str]:
    title = f"{prefix}Automation Rule Triggered: {rule.name}"
    body = (
        f"Document: {work_item.original_filename}\n"
        f"Rule: {rule.name}\n"
        f"Status: Matched\n\n"
        f"{work_item.summary or 'No summary available.'}"
    )
    return title, body


async def _run_action(
    *,
    action: Any,
    rule: AutomationRule,
    work_item: WorkItem,
    email_settings: _LazyEmailSettings,
    title_prefix: str = "",
) -> str:
    act_type = _get_condition_attribute(action, "action_type")
    act_config = _get_condition_attribute(action, "config") or {}
    normalised = str(act_type or "").lower().strip()

    if not normalised:
        raise ActionFailure("Action has no action_type.", recoverable=False)

    if normalised in EMAIL_ACTION_TYPES:
        resolved = email_settings.require()
        recipient = str(act_config.get("recipient", "")).strip()
        if not recipient:
            raise ActionFailure(f"Action '{normalised}' has no recipient configured.")
        title, body = _render_action_message(rule=rule, work_item=work_item, prefix=title_prefix)
        success = await notification_dispatcher.send(
            action_type=normalised,
            settings=resolved,
            recipient=recipient,
            title=title,
            body=body,
        )
        if not success:
            raise ActionFailure(f"Provider '{normalised}' reported delivery failure for {recipient}.")
        return f"{normalised} -> {recipient}"

    raise ActionFailure(f"Unsupported action type '{act_type}'.")


class AutomationService:
    async def execute_rules_for_work_item(
        self,
        db: Session,
        *,
        work_item_id: uuid.UUID,
        event: str,
    ) -> dict[str, int]:
        """NOTE: SUPERSEDED by the ARCH-13 DAG engine.
        Retained because test_arch13_gate_13_1_13_2_trigger_substrate.py pins lazy email settings.
        """
        stats = {"evaluated": 0, "matched": 0, "succeeded": 0, "failed": 0, "actions_failed": 0}
        work_item = db.execute(select(WorkItem).where(WorkItem.id == work_item_id)).scalar_one_or_none()
        if work_item is None:
            return stats

        workspace_id = work_item.workspace_id
        raw_rules = crud.list_active_rules_for_event(db, workspace_id=workspace_id, event=event)
        rules = sorted(raw_rules, key=lambda r: (r.priority, r.created_at.timestamp() if getattr(r, "created_at", None) else 0))
        email_settings = _LazyEmailSettings(db=db, workspace_id=workspace_id)

        for rule in rules:
            stats["evaluated"] += 1
            try:
                if not _evaluate_rule_conditions(rule, work_item):
                    continue
                stats["matched"] += 1

                actions = getattr(rule, "actions", []) or []
                action_logs: list[str] = []
                action_failures: list[str] = []

                for idx, action in enumerate(actions):
                    try:
                        action_logs.append(await _run_action(action=action, rule=rule, work_item=work_item, email_settings=email_settings))
                    except ActionFailure as exc:
                        stats["actions_failed"] += 1
                        action_failures.append(f"#{idx + 1}: {exc}")
                        if not exc.recoverable:
                            break
                    except Exception as exc:
                        stats["actions_failed"] += 1
                        action_failures.append(f"#{idx + 1}: {exc}")

                outcome = "FAILED" if (action_failures and not action_logs) else ("PARTIAL" if action_failures else "SUCCESS")

                # V-1: Legacy automation_logs write removed, replaced with structured logging
                logger.info(
                    "automation.v1_rule_evaluated",
                    extra={"rule_id": str(rule.id), "work_item_id": str(work_item.id), "outcome": outcome},
                )

                if outcome == "FAILED":
                    stats["failed"] += 1
                else:
                    stats["succeeded"] += 1

            except Exception as exc:
                stats["failed"] += 1
                db.rollback()
                logger.warning(
                    "automation.v1_rule_failed",
                    extra={"rule_id": str(rule.id), "work_item_id": str(work_item.id), "error": str(exc)},
                )

        return stats

    async def test_rule_for_work_item(
        self,
        db: Session,
        *,
        rule: AutomationRule,
        work_item: WorkItem,
    ) -> dict[str, Any]:
        start_time = time.perf_counter()
        success, matched, notification_sent = True, False, False
        message = "Rule conditions were not satisfied."
        workspace_id = work_item.workspace_id

        try:
            if _evaluate_rule_conditions(rule, work_item):
                matched = True
                email_settings = _LazyEmailSettings(db=db, workspace_id=workspace_id)
                actions = getattr(rule, "actions", []) or []
                action_logs: list[str] = []
                action_failures: list[str] = []

                for idx, action in enumerate(actions):
                    try:
                        action_logs.append(
                            await _run_action(
                                action=action,
                                rule=rule,
                                work_item=work_item,
                                email_settings=email_settings,
                                title_prefix="[TEST MATCHED] ",
                            )
                        )
                    except Exception as exc:
                        action_failures.append(f"#{idx + 1}: {exc}")

                if action_logs and not action_failures:
                    notification_sent = True
                    message = f"Rule conditions met. Actions executed: {', '.join(action_logs)}"
                elif action_logs:
                    notification_sent = True
                    success = False
                    message = f"Rule conditions met. Some actions failed: {'; '.join(action_failures)}"
                elif not actions:
                    message = "Rule conditions met. The rule has no actions."
                else:
                    success = False
                    reason = email_settings.unavailable_reason
                    message = f"Rule conditions met, but no action executed. {reason or '; '.join(action_failures)}"

                # V-3: Legacy automation_logs write removed
                logger.info(
                    "automation.manual_test_completed",
                    extra={"rule_id": str(rule.id), "work_item_id": str(work_item.id)},
                )

        except Exception as exc:
            success = False
            message = f"Error evaluating manual rule: {str(exc)}"
            logger.warning("automation.manual_test_failed", extra={"rule_id": str(rule.id), "error": str(exc)})

        execution_time_ms = (time.perf_counter() - start_time) * 1000.0
        return {
            "success": success,
            "matched": matched,
            "notification_sent": notification_sent,
            "message": message,
            "execution_time_ms": round(execution_time_ms, 2),
        }


automation_service = AutomationService()
