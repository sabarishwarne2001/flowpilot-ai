"""ARCH-37 — save-time validation, storage shape and dry runs for flow rules.

Every refusal is returned as a FastAPI-style issue list
(`[{"loc": ["body", "actions", 2, "config", "endpoint_id"], "msg": ...}]`),
which the console already parses (`services/api/errors.ts`) and maps to the
card that caused it.

WHAT IS CHECKED, AND WHY HERE
=============================

  triggers         known catalog keys the tenant's plan includes
  conditions       event fields every selected trigger carries; operators
                   valid for the field's type; values where required
  actions          registered; allowed for the triggers; config valid against
                   the action's schema; capability or add-on held; author's
                   organization role sufficient; referenced endpoint or
                   destination in this organization and active; template
                   variables from the allowlist
  otherwise        only with at least one condition

The run path re-checks capability, add-on and resource state, because each can
change after a rule is saved.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from pydantic import ValidationError

from app.services.automation import actions as action_registry
from app.services.automation import triggers as catalog
from app.services.automation.actions.base import (
    ROLE_ORGANIZATION_ADMIN,
    SaveContext,
    invalid_variables,
)

MAX_ACTIONS = 10
DOCUMENT_ACTIONS = frozenset(
    {"redaction.start", "review.escalate", "autonomy.decide", "work_item.mutate"}
)
ALL_OPERATORS = frozenset(op for ops in catalog.OPERATORS_BY_TYPE.values() for op in ops)


class FlowValidationError(ValueError):
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        super().__init__("; ".join(f"{'.'.join(map(str, i['loc']))}: {i['msg']}" for i in issues))
        self.issues = issues


@dataclass(frozen=True)
class Authoring:
    """Who is saving, and what their tenant holds."""

    db: Any
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    organization_role: str
    granted_capabilities: frozenset[str]
    addons: frozenset[str]


@dataclass
class NormalisedRule:
    event: str
    trigger_keys: list[str]
    event_types: list[str]
    conditions: list[dict[str, Any]]
    logic_operator: str
    actions: list[dict[str, Any]]
    flow_spec: Optional[dict[str, Any]]
    on_error: str
    issues: list[dict[str, Any]] = field(default_factory=list)


def _issue(loc: Sequence[Any], msg: str, kind: str = "value_error") -> dict[str, Any]:
    return {"loc": ["body", *loc], "msg": msg, "type": kind}


def _dump(model: Any) -> Any:
    return model.model_dump(mode="json") if hasattr(model, "model_dump") else model


def _role_ok(minimum: str, organization_role: str) -> bool:
    if minimum == ROLE_ORGANIZATION_ADMIN:
        return organization_role in ("OWNER", "ADMIN")
    return True


def _validate_conditions(
    groups: list[dict[str, Any]], trigger_keys: list[str], issues: list[dict[str, Any]]
) -> None:
    event_fields = {f.path: f for f in catalog.fields_for(trigger_keys)}
    specs, _ = catalog.resolve_trigger_keys(trigger_keys)
    documents = all(spec.has_document for spec in specs)
    for g_index, group in enumerate(groups):
        for c_index, condition in enumerate(group.get("conditions") or []):
            loc = ("condition_groups", g_index, "conditions", c_index)
            path = str(condition.get("field") or "")
            operator = str(condition.get("operator") or "").upper()
            value = str(condition.get("value") or "")
            if path.startswith(catalog.EVENT_FIELD_PREFIX):
                spec_field = event_fields.get(path)
                if spec_field is None:
                    issues.append(_issue((*loc, "field"), f"{path!r} is not carried by every selected trigger."))
                    continue
                allowed = catalog.OPERATORS_BY_TYPE[spec_field.type]
                if operator not in allowed:
                    issues.append(_issue((*loc, "operator"), f"{operator} does not apply to a {spec_field.type} field."))
            else:
                if not documents:
                    issues.append(_issue((*loc, "field"), "The selected trigger has no document to read."))
                if operator not in ALL_OPERATORS:
                    issues.append(_issue((*loc, "operator"), f"Unknown operator {operator!r}."))
            if operator not in catalog.VALUELESS_OPERATORS and not value.strip():
                issues.append(_issue((*loc, "value"), "A value is required for this operator."))


def _validate_actions(
    key: str,
    actions: list[dict[str, Any]],
    trigger_keys: list[str],
    authoring: Authoring,
    issues: list[dict[str, Any]],
    *,
    legacy: bool,
) -> list[dict[str, Any]]:
    excluded = catalog.excluded_actions_for(trigger_keys)
    specs, _ = catalog.resolve_trigger_keys(trigger_keys)
    documents = all(spec.has_document for spec in specs)
    event_keys = {f.path for f in catalog.fields_for(trigger_keys)}
    ctx = SaveContext(
        db=authoring.db,
        organization_id=authoring.organization_id,
        workspace_id=authoring.workspace_id,
        trigger_keys=tuple(trigger_keys),
    )
    stored: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        loc = (key, index)
        raw_type = str(action.get("action_type") or "")
        definition = action_registry.get(raw_type)
        if definition is None:
            issues.append(_issue((*loc, "action_type"), f"Unknown action {raw_type!r}."))
            continue
        action_type = definition.action_type
        if action_type in excluded:
            issues.append(_issue((*loc, "action_type"), f"{definition.label} cannot run on the selected trigger."))
        if action_type in DOCUMENT_ACTIONS and not documents:
            issues.append(_issue((*loc, "action_type"), f"{definition.label} needs a document."))
        if definition.capability and definition.capability not in authoring.granted_capabilities:
            issues.append(_issue((*loc, "action_type"), f"{definition.label} is not included in your plan.", "plan"))
        if definition.addon and definition.addon not in authoring.addons:
            issues.append(_issue((*loc, "action_type"), f"{definition.label} needs an add-on your organization does not have.", "plan"))
        if not _role_ok(definition.minimum_role, authoring.organization_role):
            issues.append(_issue((*loc, "action_type"), f"Only organization owners and admins can add {definition.label}.", "permission"))

        config_values = dict(action.get("config") or {})
        try:
            config = definition.parse(config_values)
        except ValidationError as exc:
            for error in exc.errors():
                issues.append(_issue((*loc, "config", *error["loc"]), error["msg"]))
            continue

        for template_field in definition.template_fields:
            bad = invalid_variables(str(getattr(config, template_field, "") or ""), event_keys=event_keys)
            if bad:
                issues.append(_issue((*loc, "config", template_field), f"Unknown template variable(s): {', '.join(bad)}."))

        if definition.validate_resources is not None:
            for config_key, message in definition.validate_resources(ctx, config).items():
                issues.append(_issue((*loc, "config", config_key), message))

        if legacy and raw_type != action_type:
            # An ARCH-13 client: keep its stored shape so its message is unchanged.
            stored.append({"action_type": raw_type, "config": config_values})
        else:
            stored.append({"action_type": action_type, "config": _dump(config)})
    return stored


def normalise(payload: dict[str, Any], authoring: Authoring) -> NormalisedRule:
    """Validate a complete rule document and return its storage shape."""
    issues: list[dict[str, Any]] = []
    trigger_keys = payload.get("triggers")
    legacy = trigger_keys is None

    if legacy:
        legacy_event = str(payload.get("event") or "")
        mapped = catalog.LEGACY_EVENT_TO_TRIGGER.get(legacy_event)
        if mapped is None:
            issues.append(_issue(("event",), f"Unknown event {legacy_event!r}."))
            trigger_keys = []
        else:
            trigger_keys = [mapped]
    else:
        trigger_keys = list(dict.fromkeys(trigger_keys))
        specs, unknown = catalog.resolve_trigger_keys(trigger_keys)
        for key in unknown:
            issues.append(_issue(("triggers", trigger_keys.index(key)), f"Unknown trigger {key!r}."))
        for spec in specs:
            if spec.capability and spec.capability not in authoring.granted_capabilities:
                issues.append(_issue(("triggers", trigger_keys.index(spec.key)), f"{spec.label} is not included in your plan.", "plan"))

    on_error = str(payload.get("on_error") or "HALT")
    actions = [_dump(a) for a in payload.get("actions") or []]
    else_actions = [_dump(a) for a in payload.get("else_actions") or []]
    if not actions:
        issues.append(_issue(("actions",), "Add at least one action."))
    if len(actions) > MAX_ACTIONS or len(else_actions) > MAX_ACTIONS:
        issues.append(_issue(("actions",), f"At most {MAX_ACTIONS} actions per branch."))

    if legacy:
        conditions = [_dump(c) for c in payload.get("conditions") or []]
        groups = [{"logic_operator": payload.get("logic_operator") or "AND", "conditions": conditions}]
        groups_operator = "AND"
    else:
        groups = [_dump(g) for g in payload.get("condition_groups") or []]
        groups = [g for g in groups if g.get("conditions")]
        groups_operator = str(payload.get("groups_operator") or "AND")
    _validate_conditions(groups, trigger_keys, issues)

    has_conditions = any(g.get("conditions") for g in groups)
    if else_actions and not has_conditions:
        issues.append(_issue(("else_actions",), "'Otherwise' actions need at least one condition."))

    stored_actions = _validate_actions("actions", actions, trigger_keys, authoring, issues, legacy=legacy)
    stored_else = _validate_actions("else_actions", else_actions, trigger_keys, authoring, issues, legacy=False)

    if issues:
        raise FlowValidationError(issues)

    from app.services.automation.conditions import flatten

    event_types = catalog.event_types_for(trigger_keys)
    if legacy:
        return NormalisedRule(
            event=str(payload.get("event")),
            trigger_keys=trigger_keys,
            event_types=event_types,
            conditions=groups[0]["conditions"],
            logic_operator=str(groups[0]["logic_operator"]),
            actions=stored_actions,
            flow_spec=None,
            on_error=on_error,
        )
    single_group = len(groups) == 1
    return NormalisedRule(
        event=trigger_keys[0],
        trigger_keys=trigger_keys,
        event_types=event_types,
        conditions=flatten(groups),
        logic_operator=str(groups[0]["logic_operator"]) if single_group else groups_operator,
        actions=stored_actions,
        flow_spec={
            "version": 1,
            "condition_groups": groups,
            "groups_operator": groups_operator,
            "else_actions": stored_else,
        },
        on_error=on_error,
    )


def payload_from_rule(rule: Any, event_types: list[str]) -> dict[str, Any]:
    """The rule as a complete flow document, for merging a partial update."""
    spec = rule.flow_spec if isinstance(rule.flow_spec, dict) else None
    if spec is None:
        return {
            "event": rule.event,
            "conditions": list(rule.conditions or []),
            "logic_operator": rule.logic_operator,
            "actions": list(rule.actions or []),
            "on_error": rule.on_error,
        }
    return {
        "triggers": catalog.trigger_keys_for_events(event_types),
        "condition_groups": list(spec.get("condition_groups") or []),
        "groups_operator": spec.get("groups_operator") or "AND",
        "actions": list(rule.actions or []),
        "else_actions": list(spec.get("else_actions") or []),
        "on_error": rule.on_error,
    }


def rule_view(rule: Any, event_types: list[str]) -> dict[str, Any]:
    spec = rule.flow_spec if isinstance(rule.flow_spec, dict) else None
    if spec is not None:
        groups = list(spec.get("condition_groups") or [])
        groups_operator = spec.get("groups_operator") or "AND"
        else_actions = list(spec.get("else_actions") or [])
    else:
        groups = (
            [{"logic_operator": rule.logic_operator, "conditions": list(rule.conditions or [])}]
            if rule.conditions
            else []
        )
        groups_operator = "AND"
        else_actions = []
    return {
        "id": rule.id,
        "workspace_id": rule.workspace_id,
        "created_by_user_id": rule.created_by_user_id,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
        "name": rule.name,
        "priority": rule.priority,
        "event": rule.event,
        "is_active": rule.is_active,
        "conditions": list(rule.conditions or []),
        "logic_operator": rule.logic_operator,
        "actions": list(rule.actions or []),
        "triggers": catalog.trigger_keys_for_events(event_types),
        "trigger_events": list(event_types),
        "condition_groups": groups,
        "groups_operator": groups_operator,
        "else_actions": else_actions,
        "is_flow": spec is not None,
        "graph_version": int(rule.graph_version or 0),
        "on_error": rule.on_error,
    }


def dry_run(db: Any, *, rule: Any, work_item: Any) -> dict[str, Any]:
    """Evaluate the conditions on a document and report what WOULD run.

    Nothing is performed. A test run that started a redaction or posted to a
    customer's endpoint because an administrator clicked "Test" would be a
    production side effect with no trigger behind it.
    """
    from app.services.automation import conditions
    from app.services.automation.contracts import ActionNodeConfig

    started = time.perf_counter()
    spec = rule.flow_spec if isinstance(rule.flow_spec, dict) else None
    if spec is not None:
        groups = list(spec.get("condition_groups") or [])
        matched = conditions.evaluate_groups(
            groups,
            groups_operator=spec.get("groups_operator"),
            work_item=work_item,
            event_payload=None,
        )
        planned = list(rule.actions or []) if matched else list(spec.get("else_actions") or [])
        branch = "then" if matched else ("otherwise" if planned else "none")
    else:
        matched = conditions.evaluate_node_config(
            {"conditions": list(rule.conditions or []), "logic_operator": rule.logic_operator},
            work_item=work_item,
            event_payload=None,
        )
        planned = list(rule.actions or []) if matched else []
        branch = "then" if matched else "none"

    lines: list[str] = []
    ok = True
    for index, action in enumerate(planned):
        node = ActionNodeConfig.from_node_config(action)
        definition = action_registry.get(node.action_type)
        if definition is None:
            ok = False
            lines.append(f"#{index + 1} unknown action {node.action_type!r}")
            continue
        try:
            definition.parse(node.authored_parameters())
        except ValidationError as exc:
            ok = False
            lines.append(f"#{index + 1} {definition.label}: invalid config ({exc.error_count()} issue(s))")
            continue
        lines.append(f"#{index + 1} would run: {definition.label}")

    if not planned:
        message = "Conditions were not met; nothing would run."
    else:
        message = f"Conditions {'met' if matched else 'not met'} ({branch} branch). " + "; ".join(lines)
    return {
        "success": ok,
        "matched": bool(matched),
        "notification_sent": False,
        "message": message[:2000],
        "execution_time_ms": round((time.perf_counter() - started) * 1000.0, 2),
    }


__all__ = [
    "Authoring",
    "FlowValidationError",
    "NormalisedRule",
    "dry_run",
    "normalise",
    "payload_from_rule",
    "rule_view",
]
