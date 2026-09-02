"""
Automation Rules Evaluation and Matching Service for FlowPilot AI.

ARCH-13 Step 13.1 (F4). Email settings are resolved *lazily, inside the email
action*, not eagerly before rule evaluation. The previous shape returned early
from `execute_rules_for_work_item` when a workspace had no email settings or
had SMTP disabled, which meant a workspace without SMTP ran **zero** rules --
including rules whose actions have nothing to do with email.

That bug is invisible on `main` today because every registered action provider
is an email provider (`notification/dispatcher.py` registers `email` and
`send_email`, both backed by `email_notification_provider`). The moment 13.5
adds work-item mutation and 13.6 adds LLM actions, it becomes "automation
silently does nothing for workspaces that never set up SMTP", and it gets
diagnosed as an automation bug rather than an email one.

A SECOND BUG, FOUND WHILE READING (Part 4)
==========================================

`_evaluate_rule_conditions` guarded with:

    if not field_path or not operator or target_value is None:
        matched_results.append(False)
        continue

`EXISTS`, `IS_EMPTY` and `IS_NOT_EMPTY` are explicitly handled by
`_evaluate_condition` as operators that take no target value -- but this guard
rejects them *before* they reach it whenever `value` is null. The schema
validator coerces `value` to `""` on write, so rules created through the API
are unaffected; rules whose `conditions` JSONB predates that validator, or was
written directly, silently never match. The guard now knows which operators
are valueless.
"""

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


#: Operators that carry no target value. `_evaluate_condition` handles all
#: three without reading `target_value`; the pre-flight guard in
#: `_evaluate_rule_conditions` must not demand one.
VALUELESS_OPERATORS: frozenset[str] = frozenset(
    {"EXISTS", "IS_EMPTY", "IS_NOT_EMPTY"}
)

#: Action types that need workspace email settings. Everything else -- the
#: mutation and LLM actions arriving in 13.5/13.6 -- must run without them.
#: Kept in step with `notification/dispatcher.py::_providers`.
EMAIL_ACTION_TYPES: frozenset[str] = frozenset({"email", "send_email"})


class ActionFailure(RuntimeError):
    """One action failed. Carries whether the rest of the rule may continue.

    `recoverable=True` means the failure is specific to this action and the
    rule's remaining actions are still meaningful -- a missing SMTP config is
    the motivating case. `recoverable=False` means the rule's state is
    suspect and the remaining actions should not run.
    """

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


def resolve_field(work_item: WorkItem, field_path: str) -> Any:
    """Column first, then extracted_entities by dotted path.

    Handles:
      - direct column: "summary" -> work_item.summary
      - explicit prefix: "extracted_entities.document_classification" -> work_item.extracted_entities["document_classification"]
      - implicit nested: "document_classification" -> work_item.extracted_entities["document_classification"]
    """
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


def _evaluate_rule_conditions(
    rule: Any,
    work_item: WorkItem,
) -> bool:
    conditions = getattr(rule, "conditions", []) or []
    if not conditions:
        # A rule with no conditions does not fire.
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

        normalised_operator = (
            operator.upper().strip() if isinstance(operator, str) else ""
        )
        needs_value = normalised_operator not in VALUELESS_OPERATORS

        # F4 companion fix. `EXISTS` / `IS_EMPTY` / `IS_NOT_EMPTY` take no
        # target value; demanding one made them never match on any rule whose
        # stored `value` is null.
        if not field_path or not operator or (needs_value and target_value is None):
            logger.warning(
                "automation.condition_malformed",
                extra={
                    "rule_id": str(getattr(rule, "id", None)),
                    "field": field_path,
                    "operator": operator,
                    "has_value": target_value is not None,
                },
            )
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

    if logic_operator == "OR":
        return any(matched_results)
    return all(matched_results)


# =====================================================================
# F4 -- lazy email settings resolution
# =====================================================================


@dataclass
class _LazyEmailSettings:
    """Resolves workspace email settings on first use, once per execution."""

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
            self._reason = (
                "No email settings are configured for this workspace. "
                "Non-email actions in this rule were unaffected."
            )
            logger.warning(
                "automation.email_settings_missing",
                extra={"workspace_id": str(self.workspace_id)},
            )
            return
        if not resolved.is_enabled:
            self._reason = (
                "Email delivery is disabled in this workspace's settings. "
                "Non-email actions in this rule were unaffected."
            )
            logger.info(
                "automation.email_settings_disabled",
                extra={"workspace_id": str(self.workspace_id)},
            )
            return
        self._settings = resolved

    def require(self) -> EmailSettings:
        """The settings, or an `ActionFailure` naming why they are missing."""
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


def _render_action_message(
    *, rule: AutomationRule, work_item: WorkItem, prefix: str = ""
) -> tuple[str, str]:
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
    email_settings: "_LazyEmailSettings",
    title_prefix: str = "",
) -> str:
    act_type = _get_condition_attribute(action, "action_type")
    act_config = _get_condition_attribute(action, "config") or {}
    normalised = str(act_type or "").lower().strip()

    if not normalised:
        raise ActionFailure("Action has no action_type.", recoverable=False)

    if normalised in EMAIL_ACTION_TYPES:
        resolved = email_settings.require()  # F4: fails the action, not the rule set
        recipient = str(act_config.get("recipient", "")).strip()
        if not recipient:
            raise ActionFailure(
                f"Action '{normalised}' has no recipient configured."
            )
        title, body = _render_action_message(
            rule=rule, work_item=work_item, prefix=title_prefix
        )
        success = await notification_dispatcher.send(
            action_type=normalised,
            settings=resolved,
            recipient=recipient,
            title=title,
            body=body,
        )
        if not success:
            raise ActionFailure(
                f"Provider '{normalised}' reported a delivery failure for "
                f"{recipient}."
            )
        return f"{normalised} -> {recipient}"

    raise ActionFailure(f"Unsupported action type '{act_type}'.")


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
            "actions_failed": 0,
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

        # F4. Lazy email resolution.
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
                        action_logs.append(
                            await _run_action(
                                action=action,
                                rule=rule,
                                work_item=work_item,
                                email_settings=email_settings,
                            )
                        )
                    except ActionFailure as exc:
                        stats["actions_failed"] += 1
                        action_failures.append(f"#{idx + 1}: {exc}")
                        logger.warning(
                            "automation.action_failed",
                            extra={
                                "rule_id": str(rule.id),
                                "work_item_id": str(work_item.id),
                                "action_index": idx + 1,
                                "recoverable": exc.recoverable,
                                "error": str(exc),
                            },
                        )
                        if not exc.recoverable:
                            break
                    except Exception as exc:  # noqa: BLE001
                        stats["actions_failed"] += 1
                        action_failures.append(f"#{idx + 1}: {exc}")
                        logger.exception(
                            "automation.action_errored",
                            extra={
                                "rule_id": str(rule.id),
                                "action_index": idx + 1,
                            },
                        )

                if action_failures and not action_logs:
                    outcome = "FAILED"
                elif action_failures:
                    outcome = "PARTIAL"
                else:
                    outcome = "SUCCESS"

                if outcome != "FAILED":
                    recipient_user = work_item.created_by
                    if recipient_user is not None:
                        crud.create_notification(
                            db,
                            workspace_id=workspace_id,
                            notification_in=NotificationCreate(
                                user_id=recipient_user.id,
                                work_item_id=work_item.id,
                                title=f"Automation Rule Triggered: {rule.name}",
                                message=(
                                    "Conditions matched. Actions executed "
                                    "successfully."
                                    if outcome == "SUCCESS"
                                    else "Conditions matched. Some actions failed."
                                ),
                                notification_type=NotificationType.AUTOMATION,
                                priority=(
                                    NotificationPriority.INFO
                                    if outcome == "SUCCESS"
                                    else NotificationPriority.WARNING
                                ),
                                delivery_channel=NotificationChannel.IN_APP,
                                delivery_status=NotificationStatus.SENT,
                            ),
                        )

                message_parts: list[str] = []
                if action_logs:
                    message_parts.append(" | ".join(action_logs))
                if action_failures:
                    message_parts.append("FAILED: " + " | ".join(action_failures))

                crud.create_automation_log(
                    db,
                    workspace_id=workspace_id,
                    rule_id=rule.id,
                    work_item_id=work_item.id,
                    status=outcome,
                    log_message=(
                        " || ".join(message_parts)[:5000] or "All actions executed."
                    ),
                )

                if outcome == "FAILED":
                    stats["failed"] += 1
                else:
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
                    except Exception as exc:  # noqa: BLE001
                        action_failures.append(f"#{idx + 1}: {exc}")
                        logger.warning(
                            "automation.manual_test_action_failed",
                            extra={
                                "rule_id": str(rule.id),
                                "action_index": idx + 1,
                                "error": str(exc),
                            },
                        )

                if action_logs and not action_failures:
                    notification_sent = True
                    outcome = "SUCCESS"
                    message = (
                        "Rule conditions met. Manual test actions executed: "
                        f"{', '.join(action_logs)}"
                    )
                elif action_logs:
                    notification_sent = True
                    success = False
                    outcome = "PARTIAL"
                    message = (
                        "Rule conditions met. Some actions executed and some "
                        f"failed: {'; '.join(action_failures)}"
                    )
                elif not actions:
                    outcome = "SUCCESS"
                    message = "Rule conditions met. The rule has no actions."
                else:
                    success = False
                    outcome = "FAILED"
                    reason = email_settings.unavailable_reason
                    message = (
                        "Rule conditions met, but no action executed. "
                        f"{reason or '; '.join(action_failures)}"
                    )

                crud.create_automation_log(
                    db,
                    workspace_id=workspace_id,
                    rule_id=rule.id,
                    work_item_id=work_item.id,
                    status=outcome,
                    log_message=f"{log_msg} {message}"[:5000],
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
