"""ARCH-37 action `work_item.mutate` — set a field on the document.

The ARCH-13 behaviour, with two corrections:

  * The field is from an allowlist. Before ARCH-37 any attribute except four
    could be set, including `status` and `pipeline_stage`, which let a rule
    move a document through the pipeline state machine without a transition.
    Allowed now: `summary`, `original_filename`, and one extracted entity at
    `extracted_entities.<key>`.
  * The `work_item.field_changed` event it emits now carries its
    `automation.execute` job. It was emitted with no job, so a rule chained on
    another rule's mutation never ran.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, field_validator

from app.services.automation.actions.base import (
    ROLE_WORKSPACE_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    require_work_item,
)

ACTION_TYPE = "work_item.mutate"
MUTABLE_COLUMNS = ("summary", "original_filename")
ENTITY_PREFIX = "extracted_entities."
_ENTITY_KEY = re.compile(r"^extracted_entities\.[a-z0-9_]{1,64}$")


class WorkItemMutateConfig(ActionConfig):
    target_field: str = Field(min_length=1, max_length=100)
    target_value: str = Field(default="", max_length=1000)

    @field_validator("target_field")
    @classmethod
    def _allowed(cls, value: str) -> str:
        if value in MUTABLE_COLUMNS or _ENTITY_KEY.match(value):
            return value
        raise ValueError(
            f"{value!r} cannot be set by a rule. Allowed: "
            f"{', '.join(MUTABLE_COLUMNS)}, or extracted_entities.<key>."
        )


def perform(state: Any, spec: Any) -> ActionOutcome:
    from app.services import outbox_service

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, WorkItemMutateConfig)
    work_item = require_work_item(state, ACTION_TYPE)

    if config.target_field.startswith(ENTITY_PREFIX):
        key = config.target_field[len(ENTITY_PREFIX):]
        entities = dict(work_item.extracted_entities or {})
        entities[key] = config.target_value
        work_item.extracted_entities = entities
    elif config.target_field == "original_filename":
        if not config.target_value.strip():
            raise ActionFailure("A file name cannot be empty.", recoverable=False)
        work_item.original_filename = config.target_value[:255]
    else:
        work_item.summary = config.target_value
    state.db.flush([work_item])

    event = outbox_service.emit_trigger(
        state.db,
        organization_id=state.execution.organization_id,
        workspace_id=state.execution.workspace_id,
        event_type="work_item.field_changed",
        resource_id=work_item.id,
        payload={
            "work_item_id": str(work_item.id),
            "field": config.target_field,
            "rule_id": str(state.rule.id),
            "execution_id": str(state.execution.id),
        },
        caused_by=state.trigger_event,
    )
    if event is not None:
        state.emitted_event_ids.append(str(event.id))
    return ActionOutcome(
        summary=f"set_field {config.target_field}",
        external_ref=str(event.id) if event is not None else None,
        details={"field": config.target_field},
    )


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Set a document field",
    description="Write a value to the document's summary, file name or an extracted field.",
    category="Documents",
    config_model=WorkItemMutateConfig,
    selector="automation.flow.work_item_mutate",
    perform=perform,
    capability=None,
    minimum_role=ROLE_WORKSPACE_ADMIN,
    aliases=("set_field",),
    side_effect=True,
)
