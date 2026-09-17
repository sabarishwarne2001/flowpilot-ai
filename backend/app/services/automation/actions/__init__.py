"""ARCH-37 — the action registry.

`perform_action(state, spec)` is a dictionary dispatch. Before ARCH-37 the
executor's `_default_perform_action` was an if/elif over two action types, and
`resolve_selector("webhook")` returned a selector for an action nothing could
perform, so a webhook rule failed at run time with "Unsupported action type".

`_assert_registry_complete` runs at import. Every registered action must have
a config schema, a registered R33 selector, a perform function, and a declared
capability and minimum role; every alias must name one action. A module that
forgets a part stops the process from starting, not the first customer run.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.services.automation.actions import (
    autonomy_decide,
    email_send,
    notify_role,
    redaction_start,
    review_escalate,
    warehouse_export,
    webhook_send,
    work_item_mutate,
)
from app.services.automation.actions.base import (
    MINIMUM_ROLES,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
)

_MODULES = (
    webhook_send,
    redaction_start,
    review_escalate,
    warehouse_export,
    notify_role,
    autonomy_decide,
    email_send,
    work_item_mutate,
)

ACTIONS: Mapping[str, ActionDefinition] = {
    module.DEFINITION.action_type: module.DEFINITION for module in _MODULES
}

#: The seven actions ARCH-37 sells, in catalog order. `work_item.mutate`
#: predates it and is registered alongside.
COMMERCIAL_ACTION_TYPES: tuple[str, ...] = (
    "webhook.send",
    "redaction.start",
    "review.escalate",
    "warehouse.export",
    "notify.role",
    "autonomy.decide",
    "email.send",
)

ALIASES: Mapping[str, str] = {
    alias: definition.action_type
    for definition in ACTIONS.values()
    for alias in definition.aliases
}

#: Handled by the executor itself, never by this registry.
LLM_ACTION_TYPES: frozenset[str] = frozenset({"llm.extract", "llm.classify"})


def canonical(action_type: str) -> str:
    normalised = (action_type or "").strip().lower()
    return ALIASES.get(normalised, normalised)


def get(action_type: str) -> ActionDefinition | None:
    return ACTIONS.get(canonical(action_type))


def selector_for(action_type: str) -> str | None:
    definition = get(action_type)
    return definition.selector if definition is not None else None


def perform_action(state: Any, spec: Any) -> ActionOutcome:
    definition = get(spec.action_type)
    if definition is None:
        raise ActionFailure(f"Unsupported action type '{spec.action_type}'.", recoverable=False)
    outcome = definition.perform(state, spec)
    if not isinstance(outcome, ActionOutcome):
        raise TypeError(f"{definition.action_type}.perform returned {type(outcome).__name__}")
    return outcome


def _assert_registry_complete() -> None:
    from pydantic import BaseModel

    from app.services import tools as _tools  # noqa: F401
    from app.services.fenced_context import TOOL_SELECTORS
    from app.services.tools import flow_selectors  # noqa: F401  (registers)

    problems: list[str] = []
    for action_type, definition in ACTIONS.items():
        if not (isinstance(definition.config_model, type) and issubclass(definition.config_model, BaseModel)):
            problems.append(f"{action_type}: no config schema")
        if not issubclass(definition.config_model, ActionConfig):
            problems.append(f"{action_type}: config schema does not forbid unknown keys")
        if not definition.selector or definition.selector not in TOOL_SELECTORS:
            problems.append(f"{action_type}: selector {definition.selector!r} is not registered")
        if not callable(definition.perform):
            problems.append(f"{action_type}: no perform")
        if definition.minimum_role not in MINIMUM_ROLES:
            problems.append(f"{action_type}: unknown minimum role {definition.minimum_role!r}")
        if not hasattr(definition, "capability"):
            problems.append(f"{action_type}: no capability declaration")
    missing = [a for a in COMMERCIAL_ACTION_TYPES if a not in ACTIONS]
    if missing:
        problems.append(f"commercial actions not registered: {missing}")
    clashes = [alias for alias in ALIASES if alias in ACTIONS]
    if clashes:
        problems.append(f"aliases shadow action types: {clashes}")
    if len(ALIASES) != sum(len(d.aliases) for d in ACTIONS.values()):
        problems.append("an alias is claimed by two actions")
    if problems:
        raise RuntimeError("ARCH-37 action registry is incomplete: " + "; ".join(problems))


_assert_registry_complete()


__all__ = [
    "ACTIONS",
    "ALIASES",
    "COMMERCIAL_ACTION_TYPES",
    "LLM_ACTION_TYPES",
    "ActionDefinition",
    "ActionFailure",
    "ActionOutcome",
    "canonical",
    "get",
    "perform_action",
    "selector_for",
]
