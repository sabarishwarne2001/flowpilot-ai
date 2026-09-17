"""ARCH-37 — condition groups and trigger-payload fields.

A flow-builder rule stores its conditions as groups:

    {"groups_operator": "AND",
     "groups": [{"logic_operator": "OR", "conditions": [...]}, ...]}

Each condition's `field` is either a document path (resolved exactly as
before ARCH-37, by `automation_service.resolve_field`) or `event.<key>`, which
reads the trigger event's payload. Before ARCH-37 a rule could only look at the
document, so "when a match finishes with more than two exceptions" had no way
to say "more than two exceptions".

An empty group list matches: a flow rule with no conditions runs on every
occurrence of its trigger, which is what "When an anomaly is detected, notify
Finance admins" means.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from app.services.automation.triggers import EVENT_FIELD_PREFIX, VALUELESS_OPERATORS

MAX_GROUPS = 10
MAX_CONDITIONS_PER_GROUP = 20


def _attr(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _nested(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def resolve(
    field_path: str,
    *,
    work_item: Any,
    event_payload: Optional[Mapping[str, Any]],
) -> Any:
    if field_path.startswith(EVENT_FIELD_PREFIX):
        return _nested(event_payload or {}, field_path[len(EVENT_FIELD_PREFIX):])
    if work_item is None:
        return None
    from app.services.automation_service import resolve_field

    return resolve_field(work_item, field_path)


def evaluate_condition(
    condition: Any,
    *,
    work_item: Any,
    event_payload: Optional[Mapping[str, Any]],
) -> bool:
    from app.services.automation_service import _evaluate_condition

    field_path = _attr(condition, "field")
    operator = _attr(condition, "operator")
    value = _attr(condition, "value")
    if not isinstance(field_path, str) or not field_path.strip():
        return False
    if not isinstance(operator, str) or not operator.strip():
        return False
    normalised = operator.upper().strip()
    if normalised not in VALUELESS_OPERATORS and (value is None or str(value).strip() == ""):
        return False
    actual = resolve(field_path.strip(), work_item=work_item, event_payload=event_payload)
    return bool(_evaluate_condition(actual, normalised, "" if value is None else str(value)))


def _combine(results: Sequence[bool], operator: str) -> bool:
    if not results:
        return True
    return any(results) if operator == "OR" else all(results)


def _operator(value: Any) -> str:
    text = str(value or "AND").upper().strip()
    return text if text in ("AND", "OR") else "AND"


def evaluate_groups(
    groups: Sequence[Any],
    *,
    groups_operator: Any,
    work_item: Any,
    event_payload: Optional[Mapping[str, Any]],
) -> bool:
    outcomes: list[bool] = []
    for group in groups or ():
        conditions = list(_attr(group, "conditions") or [])
        if not conditions:
            continue
        results = [
            evaluate_condition(c, work_item=work_item, event_payload=event_payload)
            for c in conditions
        ]
        outcomes.append(_combine(results, _operator(_attr(group, "logic_operator"))))
    return _combine(outcomes, _operator(groups_operator))


def evaluate_node_config(
    config: Mapping[str, Any],
    *,
    work_item: Any,
    event_payload: Optional[Mapping[str, Any]],
) -> bool:
    """A condition node's config, grouped (ARCH-37) or flat (ARCH-13)."""
    if "groups" in config:
        return evaluate_groups(
            config.get("groups") or [],
            groups_operator=config.get("groups_operator"),
            work_item=work_item,
            event_payload=event_payload,
        )
    flat = list(config.get("conditions") or [])
    if not flat:
        return False
    results = [
        evaluate_condition(c, work_item=work_item, event_payload=event_payload)
        for c in flat
    ]
    return _combine(results, _operator(config.get("logic_operator")))


def flatten(groups: Sequence[Any]) -> list[dict[str, Any]]:
    """Every condition in every group, for the legacy `conditions` column."""
    flat: list[dict[str, Any]] = []
    for group in groups or ():
        for condition in _attr(group, "conditions") or ():
            flat.append(
                {
                    "field": _attr(condition, "field"),
                    "operator": _attr(condition, "operator"),
                    "value": _attr(condition, "value") or "",
                }
            )
    return flat


__all__ = [
    "MAX_CONDITIONS_PER_GROUP",
    "MAX_GROUPS",
    "evaluate_condition",
    "evaluate_groups",
    "evaluate_node_config",
    "flatten",
    "resolve",
]
