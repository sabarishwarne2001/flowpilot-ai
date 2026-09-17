"""ARCH-37 action `webhook.send` — post to one registered webhook endpoint.

GUARDRAIL: there is no URL in this action's config. A free-form URL in a
tenant-authored rule is a server-side request forgery primitive, so the rule
names a `webhook_endpoints` row, and that row must be ACTIVE, in the rule's
organization, and either organization-wide or scoped to the rule's workspace.
The check runs at save time and again at run time, because an endpoint can be
disabled or re-scoped between the two.

Delivery reuses ARCH-09 end to end: the action writes one `webhook_deliveries`
row, and the delivery loop signs it (HMAC, rotating secrets), sends it through
the SSRF-safe client, and retries with backoff.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from pydantic import Field, field_validator

from app.services.automation.actions.base import (
    ROLE_ORGANIZATION_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    SaveContext,
)

ACTION_TYPE = "webhook.send"
EVENT_TYPE = "workflow.triggered"
FIELD_PATTERN = r"^[a-z0-9_]{1,64}(\.[a-z0-9_]{1,64})?$"
MAX_FIELD_CHARS = 512


class WebhookSendConfig(ActionConfig):
    endpoint_id: uuid.UUID = Field(description="A registered, active webhook endpoint.")
    include_fields: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Extracted document fields to include, by path. Scalars only.",
    )

    @field_validator("include_fields")
    @classmethod
    def _paths(cls, value: list[str]) -> list[str]:
        bad = [f for f in value if not re.match(FIELD_PATTERN, f)]
        if bad:
            raise ValueError(f"include_fields has invalid paths: {bad}")
        if len(set(value)) != len(value):
            raise ValueError("include_fields must not repeat.")
        return value


def load_endpoint(
    db: Any,
    *,
    endpoint_id: uuid.UUID,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> tuple[Optional[Any], Optional[str]]:
    """The endpoint if this workspace may target it, else a refusal reason."""
    from sqlalchemy import select

    from app.models.webhook_endpoint import WebhookEndpoint, WebhookEndpointStatus

    endpoint = db.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.id == endpoint_id)
    ).scalar_one_or_none()
    if endpoint is None or endpoint.organization_id != organization_id:
        # Same message for "absent" and "another organization's": the
        # difference is not the author's to learn.
        return None, "Webhook endpoint not found in this organization."
    if endpoint.workspace_id is not None and endpoint.workspace_id != workspace_id:
        return None, "This webhook endpoint is scoped to a different workspace."
    if endpoint.status is not WebhookEndpointStatus.ACTIVE:
        return None, "This webhook endpoint is disabled."
    return endpoint, None


def _validate(ctx: SaveContext, config: ActionConfig) -> dict[str, str]:
    assert isinstance(config, WebhookSendConfig)
    _, reason = load_endpoint(
        ctx.db,
        endpoint_id=config.endpoint_id,
        organization_id=ctx.organization_id,
        workspace_id=ctx.workspace_id,
    )
    return {"endpoint_id": reason} if reason else {}


def _scalar(value: Any) -> Any:
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, str):
        return value[:MAX_FIELD_CHARS]
    return None


def build_payload(state: Any, config: WebhookSendConfig) -> dict[str, Any]:
    from app.services.automation.triggers import TRIGGER_BY_EVENT

    event = state.trigger_event
    event_type = getattr(event, "event_type", None)
    spec = TRIGGER_BY_EVENT.get(event_type or "")
    event_payload = dict(getattr(event, "payload", None) or {})
    work_item = state.work_item

    fields: dict[str, Any] = {}
    entities = dict(getattr(work_item, "extracted_entities", None) or {}) if work_item else {}
    for path in config.include_fields:
        current: Any = entities
        for part in path.split("."):
            current = current.get(part) if isinstance(current, dict) else None
        fields[path] = _scalar(current)

    return {
        "rule": {"id": str(state.rule.id), "name": state.rule.name},
        "execution_id": str(state.execution.id),
        "workspace_id": str(state.execution.workspace_id),
        "trigger": {
            "key": spec.key if spec else None,
            "label": spec.label if spec else None,
            "event_id": str(event.id) if event is not None else None,
        },
        "work_item": (
            {
                "id": str(work_item.id),
                "original_filename": work_item.original_filename,
                "status": getattr(work_item.status, "value", work_item.status),
            }
            if work_item is not None
            else None
        ),
        # Only the fields the catalog declares for this trigger. A dispute
        # reason or a finding headline is free text and stays inside.
        "event": {
            f.key: _scalar(event_payload.get(f.key))
            for f in (spec.fields if spec else ())
            if f.key not in ("failure_reason",)
        },
        "fields": fields,
    }


def perform(state: Any, spec: Any) -> ActionOutcome:
    from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, WebhookSendConfig)
    endpoint, reason = load_endpoint(
        state.db,
        endpoint_id=config.endpoint_id,
        organization_id=state.execution.organization_id,
        workspace_id=state.execution.workspace_id,
    )
    if endpoint is None:
        raise ActionFailure(reason or "Webhook endpoint unavailable.", recoverable=False)

    delivery = WebhookDelivery(
        webhook_endpoint_id=endpoint.id,
        outbox_event_id=None,
        organization_id=state.execution.organization_id,
        event_type=EVENT_TYPE,
        payload=build_payload(state, config),
        status=WebhookDeliveryStatus.PENDING,
    )
    state.db.add(delivery)
    state.db.flush([delivery])
    return ActionOutcome(
        summary=f"webhook queued for endpoint {endpoint.id}",
        external_ref=str(delivery.id),
        details={"endpoint_id": str(endpoint.id)},
    )


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Send to webhook",
    description="Post a signed event to one of your organization's registered webhook endpoints.",
    category="Integrations",
    config_model=WebhookSendConfig,
    selector="automation.flow.webhook_send",
    perform=perform,
    capability=None,
    minimum_role=ROLE_ORGANIZATION_ADMIN,
    aliases=("webhook",),
    validate_resources=_validate,
)
