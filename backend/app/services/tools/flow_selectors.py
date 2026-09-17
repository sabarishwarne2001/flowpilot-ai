"""ARCH-37 — one R33 selector per flow-builder action type.

Each selector copies the author's configuration into an `ActionSpec` and then
proves, with `assert_no_document_derived_values`, that nothing in the spec came
from a document. The facts are passed in only so a selector can record which
keys existed (`rationale`); their values never reach the spec.

The action registry (`app/services/automation/actions`) names these selectors
by string and refuses to import if one is missing, so an action cannot be
registered without passing through this boundary.
"""

from __future__ import annotations

import logging

from app.services.automation.contracts import (
    ActionNodeConfig,
    ActionSpec,
    FactSet,
    TenantScope,
    register_tool_selector,
)

logger = logging.getLogger("app.services.tools.flow_selectors")

SELECTOR_PREFIX = "automation.flow."


def _select(
    name: str,
    *,
    node_config: ActionNodeConfig,
    facts: FactSet,
    tenant: TenantScope,
) -> ActionSpec:
    spec = ActionSpec(
        action_type=node_config.action_type,
        recipient=node_config.recipient,
        target_field=node_config.target_field,
        target_value=node_config.target_value,
        rationale=tuple(facts.keys()),
        parameters=tuple(node_config.options),
        list_parameters=tuple(node_config.list_options),
    )
    spec.assert_no_document_derived_values(config=node_config, facts=facts)
    logger.debug(
        "tools.flow_action_selected",
        extra={
            "selector": name,
            "action_type": node_config.action_type,
            "workspace_id": str(tenant.workspace_id),
            "rule_id": str(tenant.rule_id),
            "execution_id": str(tenant.execution_id),
        },
    )
    return spec


@register_tool_selector("automation.flow.webhook_send")
def select_webhook_send(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    return _select("automation.flow.webhook_send", node_config=node_config, facts=facts, tenant=tenant)


@register_tool_selector("automation.flow.redaction_start")
def select_redaction_start(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    return _select("automation.flow.redaction_start", node_config=node_config, facts=facts, tenant=tenant)


@register_tool_selector("automation.flow.review_escalate")
def select_review_escalate(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    return _select("automation.flow.review_escalate", node_config=node_config, facts=facts, tenant=tenant)


@register_tool_selector("automation.flow.warehouse_export")
def select_warehouse_export(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    return _select("automation.flow.warehouse_export", node_config=node_config, facts=facts, tenant=tenant)


@register_tool_selector("automation.flow.notify_role")
def select_notify_role(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    return _select("automation.flow.notify_role", node_config=node_config, facts=facts, tenant=tenant)


@register_tool_selector("automation.flow.autonomy_decide")
def select_autonomy_decide(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    return _select("automation.flow.autonomy_decide", node_config=node_config, facts=facts, tenant=tenant)


@register_tool_selector("automation.flow.email_send")
def select_email_send(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    if not node_config.recipient:
        raise ValueError("An email action needs an author-supplied recipient.")
    return _select("automation.flow.email_send", node_config=node_config, facts=facts, tenant=tenant)


@register_tool_selector("automation.flow.work_item_mutate")
def select_work_item_mutate(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    if node_config.target_field is None:
        raise ValueError(
            "A mutation action needs an author-supplied target_field. Deriving "
            "the field name from extraction would let a document choose which "
            "column it writes to."
        )
    return _select("automation.flow.work_item_mutate", node_config=node_config, facts=facts, tenant=tenant)


__all__ = [
    "SELECTOR_PREFIX",
    "select_autonomy_decide",
    "select_email_send",
    "select_notify_role",
    "select_redaction_start",
    "select_review_escalate",
    "select_warehouse_export",
    "select_webhook_send",
    "select_work_item_mutate",
]
