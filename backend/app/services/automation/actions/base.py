"""ARCH-37 — the contract every flow-builder action implements.

One module per action type, each exporting a `DEFINITION` with four parts the
registry refuses to import without:

    config_model   a pydantic model; the save path and the run path both
                   validate the author's config against it
    selector       the name of an R33 selector in
                   app/services/tools/flow_selectors.py
    perform        perform(state, spec) -> ActionOutcome
    capability / minimum_role
                   what the tenant must hold to run it and who may author it

`perform` receives the executor's `_WalkState` and the selector's
`ActionSpec`. It never reads the node config directly: the spec is the only
path from the author to the effect, and the selector is what checked it.
"""

from __future__ import annotations

import html
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

#: Who may put an action into a rule.
ROLE_WORKSPACE_ADMIN = "WORKSPACE_ADMIN"
ROLE_ORGANIZATION_ADMIN = "ORGANIZATION_ADMIN"
MINIMUM_ROLES = frozenset({ROLE_WORKSPACE_ADMIN, ROLE_ORGANIZATION_ADMIN})


class ActionFailure(RuntimeError):
    """An action could not run. Mirrors automation_service.ActionFailure."""

    def __init__(self, message: str, *, recoverable: bool = True) -> None:
        super().__init__(message)
        self.recoverable = recoverable


class ActionConfig(BaseModel):
    """Base for every action config. Unknown keys are refused."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@dataclass
class ActionOutcome:
    summary: str
    external_ref: Optional[str] = None
    #: False stops every node after this one: `autonomy.decide` holding a
    #: document is the branch on outcome the builder offers.
    continue_downstream: bool = True
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # executors that expect a string still get one
        return self.summary


@dataclass(frozen=True)
class SaveContext:
    """What save-time validation may consult."""

    db: Any
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    trigger_keys: tuple[str, ...]


@dataclass(frozen=True)
class ActionDefinition:
    action_type: str
    label: str
    description: str
    category: str
    config_model: type[ActionConfig]
    selector: str
    perform: Callable[[Any, Any], ActionOutcome]
    capability: Optional[str]
    minimum_role: str
    addon: Optional[str] = None
    aliases: tuple[str, ...] = ()
    #: Config keys that hold templates. Their variables are checked on save.
    template_fields: tuple[str, ...] = ()
    #: Extra save-time checks that need the database (ownership of an
    #: endpoint, a destination). Returns {config_key: message}.
    validate_resources: Optional[Callable[[SaveContext, ActionConfig], dict[str, str]]] = None
    #: True when `perform` has an external effect a test run must not cause.
    side_effect: bool = True

    def parse(self, values: Mapping[str, Any]) -> ActionConfig:
        return self.config_model.model_validate(dict(values))

    def parse_spec(self, spec: Any) -> ActionConfig:
        try:
            return self.parse(spec.parameter_dict())
        except ValidationError as exc:
            raise ActionFailure(
                f"{self.action_type} config is invalid: "
                + "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
                ),
                recoverable=False,
            ) from exc

    def schema(self) -> dict[str, Any]:
        return self.config_model.model_json_schema()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_VARIABLE = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
_SAFE_FIELD = re.compile(r"^field\.[a-z0-9_]{1,64}(\.[a-z0-9_]{1,64})?$")
_SAFE_EVENT = re.compile(r"^event\.[a-z0-9_]{1,64}$")
MAX_VARIABLE_CHARS = 256

STATIC_VARIABLES: tuple[str, ...] = (
    "document.filename",
    "document.status",
    "document.classification",
    "rule.name",
    "trigger.label",
)


def template_variables(template: str) -> list[str]:
    return _VARIABLE.findall(template or "")


def invalid_variables(template: str, *, event_keys: set[str]) -> list[str]:
    """Variables a template may not use: not static, not the selected
    triggers' event fields, and not a well-formed document field path."""
    bad: list[str] = []
    for name in template_variables(template):
        if name in STATIC_VARIABLES:
            continue
        if _SAFE_EVENT.match(name) and name in event_keys:
            continue
        if _SAFE_FIELD.match(name):
            continue
        bad.append(name)
    return bad


def _scalar(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    text = str(value)
    return text[:MAX_VARIABLE_CHARS]


class TemplateValues(dict):
    """Resolved variables. `field.<path>` is read from the document on demand."""

    def __init__(self, values: Mapping[str, str], entities: Mapping[str, Any]) -> None:
        super().__init__(values)
        self._entities = entities

    def __missing__(self, key: str) -> str:
        if not _SAFE_FIELD.match(key):
            return ""
        current: Any = self._entities
        for part in key[len("field."):].split("."):
            if not isinstance(current, Mapping):
                return ""
            current = current.get(part)
        return html.escape(_scalar(current), quote=True)


def variables_for(state: Any) -> TemplateValues:
    """Runtime values. Scalars only, truncated, and HTML-escaped."""
    from app.services.automation.triggers import TRIGGER_BY_EVENT

    work_item = getattr(state, "work_item", None)
    event = getattr(state, "trigger_event", None)
    payload = dict(getattr(event, "payload", None) or {})
    entities = dict(getattr(work_item, "extracted_entities", None) or {}) if work_item else {}
    spec = TRIGGER_BY_EVENT.get(getattr(event, "event_type", "") or "")
    classification = entities.get("classification_details")
    status = getattr(work_item, "status", None)

    values: dict[str, str] = {
        "document.filename": _scalar(getattr(work_item, "original_filename", None)),
        "document.status": _scalar(getattr(status, "value", status)),
        "document.classification": _scalar(
            classification.get("document_classification")
            if isinstance(classification, Mapping)
            else None
        ),
        "rule.name": _scalar(getattr(getattr(state, "rule", None), "name", None)),
        "trigger.label": spec.label if spec else "",
    }
    for key, value in payload.items():
        values[f"event.{key}"] = _scalar(value)
    escaped = {k: html.escape(v, quote=True) for k, v in values.items()}
    return TemplateValues(escaped, entities)


def render(template: str, values: Mapping[str, str]) -> str:
    return _VARIABLE.sub(lambda m: values[m.group(1)] if isinstance(values, TemplateValues) else values.get(m.group(1), ""), template or "")


def require_work_item(state: Any, action_type: str) -> Any:
    work_item = getattr(state, "work_item", None)
    if work_item is None:
        raise ActionFailure(f"{action_type} needs a document, and this trigger has none.", recoverable=False)
    return work_item


def has_capability(state: Any, capability: Optional[str]) -> bool:
    if capability is None:
        return True
    from app.api import capability_gate

    return capability_gate.has_capability(
        state.db,
        organization_id=state.execution.organization_id,
        capability_key=capability,
    )


__all__ = [
    "ActionConfig",
    "ActionDefinition",
    "ActionFailure",
    "ActionOutcome",
    "MINIMUM_ROLES",
    "ROLE_ORGANIZATION_ADMIN",
    "ROLE_WORKSPACE_ADMIN",
    "STATIC_VARIABLES",
    "SaveContext",
    "TemplateValues",
    "has_capability",
    "invalid_variables",
    "render",
    "require_work_item",
    "template_variables",
    "variables_for",
]
