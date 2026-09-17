"""ARCH-37 action `email.send` — the pre-ARCH-37 email action, templated.

The delivery path is unchanged: the workspace's automation email settings and
`notification_dispatcher.send`. What is new is the subject and body, which are
templates over an allowlist of variables (document, rule, trigger, the
trigger's declared event fields, and scalar document fields). Every value is
HTML-escaped and truncated before it is substituted; an unknown variable is
refused when the rule is saved.

`email` and `send_email` (and the console's old `SEND_EMAIL`) are aliases, so
every rule written before ARCH-37 keeps running through this module.
"""

from __future__ import annotations

from typing import Any

from pydantic import EmailStr, Field

from app.services.automation.actions.base import (
    ROLE_WORKSPACE_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    render,
    variables_for,
)

ACTION_TYPE = "email.send"
DEFAULT_SUBJECT = "Automation Rule Triggered: {{rule.name}}"
DEFAULT_BODY = (
    "Document: {{document.filename}}\n"
    "Rule: {{rule.name}}\n"
    "Trigger: {{trigger.label}}\n"
)


class EmailSendConfig(ActionConfig):
    recipient: EmailStr
    subject: str = Field(default=DEFAULT_SUBJECT, min_length=1, max_length=150)
    body: str = Field(default=DEFAULT_BODY, min_length=1, max_length=4000)


def perform(state: Any, spec: Any) -> ActionOutcome:
    import asyncio

    from app.services.automation_service import _LazyEmailSettings, _render_action_message
    from app.services.notification.dispatcher import notification_dispatcher

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, EmailSendConfig)
    settings = _LazyEmailSettings(db=state.db, workspace_id=state.execution.workspace_id).require()

    legacy = spec.action_type in ("email", "send_email") and not dict(spec.parameters).get("body")
    if legacy and state.work_item is not None:
        # Byte-for-byte the message a pre-ARCH-37 rule sent.
        title, body = _render_action_message(rule=state.rule, work_item=state.work_item)
    else:
        values = variables_for(state)
        title = render(config.subject, values)[:150] or state.rule.name
        body = render(config.body, values)

    ok = asyncio.run(
        notification_dispatcher.send(
            action_type="email",
            settings=settings,
            recipient=str(config.recipient),
            title=title,
            body=body,
        )
    )
    if not ok:
        raise ActionFailure("The mail provider reported a delivery failure.")
    return ActionOutcome(summary=f"email -> {config.recipient}", details={"channel": "email"})


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Send email",
    description="Send an email through the workspace's automation email settings.",
    category="Notifications",
    config_model=EmailSendConfig,
    selector="automation.flow.email_send",
    perform=perform,
    capability=None,
    minimum_role=ROLE_WORKSPACE_ADMIN,
    aliases=("email", "send_email"),
    template_fields=("subject", "body"),
)
