"""ARCH-13 Step 13.6: the first real occupants of the R33 boundary."""

from __future__ import annotations

import logging
from typing import Optional

from app.services.automation.contracts import (
    ActionNodeConfig,
    ActionSpec,
    FactSet,
    TenantScope,
    register_tool_selector,
)

logger = logging.getLogger("app.services.tools.action_selectors")


@register_tool_selector("automation.action_selector")
def select_action(
    *,
    node_config: ActionNodeConfig,
    facts: FactSet,
    tenant: TenantScope,
) -> ActionSpec:
    """Choose which action runs, and with what values."""
    spec = ActionSpec(
        action_type=node_config.action_type,
        recipient=node_config.recipient,
        target_field=node_config.target_field,
        target_value=node_config.target_value,
        rationale=tuple(facts.keys()),
    )
    spec.assert_no_document_derived_values(config=node_config, facts=facts)

    logger.debug(
        "tools.action_selected",
        extra={
            "selector": "automation.action_selector",
            "action_type": node_config.action_type,
            "workspace_id": str(tenant.workspace_id),
            "rule_id": str(tenant.rule_id),
            "execution_id": str(tenant.execution_id),
        },
    )
    return spec


@register_tool_selector("automation.mutation_selector")
def select_mutation(
    *,
    node_config: ActionNodeConfig,
    facts: FactSet,
    tenant: TenantScope,
) -> ActionSpec:
    """Choose a work-item field mutation."""
    if node_config.target_field is None:
        raise ValueError(
            "A mutation action needs an author-supplied target_field. "
            "Deriving the field name from extraction would let a "
            "document choose which column it writes to."
        )

    spec = ActionSpec(
        action_type=node_config.action_type,
        target_field=node_config.target_field,
        target_value=node_config.target_value,
        rationale=tuple(facts.keys()),
    )
    spec.assert_no_document_derived_values(config=node_config, facts=facts)

    logger.debug(
        "tools.mutation_selected",
        extra={
            "selector": "automation.mutation_selector",
            "target_field": node_config.target_field,
            "workspace_id": str(tenant.workspace_id),
            "rule_id": str(tenant.rule_id),
            "execution_id": str(tenant.execution_id),
        },
    )
    return spec


def resolve_selector(action_type: str) -> Optional[str]:
    """Which registered selector handles an action type."""
    normalised = (action_type or "").strip().lower()
    if normalised in ("email", "send_email", "webhook"):
        return "automation.action_selector"
    if normalised in ("set_field", "work_item.mutate"):
        return "automation.mutation_selector"
    return None


__all__ = [
    "resolve_selector",
    "select_action",
    "select_mutation",
]
