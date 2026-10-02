"""Injection into automations — hostile document text cannot become code, a URL or a recipient.

    pytest tests/security/test_automation_template_injection.py -q

A workflow reacts to a document whose CONTENT an outsider wrote (a supplier's
invoice, a scanned letter): its filename, and any extracted field, may say
anything. Prompt injection into the model that extracts fields can therefore put
arbitrary text into those values. What matters is what the automation layer lets
that text DO. The design answers it in three ways, pinned here:

* a template is a regex substitution over an allow-list of variables, not an
  engine, so there is nothing to execute; values are one pass, scalar-only,
  truncated and HTML-escaped, and are never re-expanded;
* the recipient of `email.send` and the endpoint of `webhook.send` are chosen
  by the rule's AUTHOR (a workspace administrator), never taken from a
  document field, and `webhook.send` has no URL at all;
* an unknown variable is refused when the rule is saved.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services.automation.actions import base
from app.services.automation.actions.email_send import EmailSendConfig
from app.services.automation.actions.webhook_send import WebhookSendConfig

pytestmark = pytest.mark.no_db

HOSTILE_VALUE = "{{rule.name}} <script>alert(1)</script> {{field.vendor}} ${7*7} {% import os %}"


def _state(filename: str = HOSTILE_VALUE, **entities):
    return SimpleNamespace(
        work_item=SimpleNamespace(
            original_filename=filename,
            status="COMPLETED",
            extracted_entities={"classification_details": {"document_classification": HOSTILE_VALUE}, **entities},
        ),
        trigger_event=None,
        rule=SimpleNamespace(name="Approve small invoices"),
    )


def test_a_hostile_filename_is_escaped_and_never_re_expanded() -> None:
    values = base.variables_for(_state())
    rendered = base.render("File: {{document.filename}}", values)
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    # The literal text {{rule.name}} that arrived inside the value stays literal:
    assert "Approve small invoices" not in rendered
    assert "{{rule.name}}" in rendered


def test_hostile_extracted_fields_are_escaped_and_truncated() -> None:
    values = base.variables_for(_state(vendor="<img src=x onerror=alert(1)>" + "A" * 1000))
    rendered = base.render("{{field.vendor}}", values)
    assert "<img" not in rendered and len(rendered) <= base.MAX_VARIABLE_CHARS * 6  # escaped entities may lengthen it


@pytest.mark.parametrize(
    "template",
    ["{{7*7}}", "{% import os %}", "${jndi:ldap://x/a}", "{{ ''.__class__.__mro__ }}", "{{config.items()}}", "#{7*7}"],
)
def test_there_is_no_template_engine_to_execute_anything(template: str) -> None:
    values = base.variables_for(_state())
    assert base.render(template, values) == template


@pytest.mark.parametrize(
    "name",
    ["__class__", "settings.JWT_SECRET_KEY", "document.__dict__", "document", "rule.owner.password", "field.a.b.c", "field.UPPER", "env.HOME"],
)
def test_an_unknown_variable_is_refused_when_the_rule_is_saved(name: str) -> None:
    assert base.invalid_variables("Hello {{" + name + "}}", event_keys=set()) == [name]


def test_a_path_like_token_is_not_even_a_variable() -> None:
    values = base.variables_for(_state())
    assert base.template_variables("{{field.../../etc/passwd}}") == []
    assert base.render("{{field.../../etc/passwd}}", values) == "{{field.../../etc/passwd}}"


def test_known_variables_are_accepted() -> None:
    template = "{{document.filename}} {{rule.name}} {{trigger.label}} {{field.invoice_total}} {{event.amount}}"
    assert base.invalid_variables(template, event_keys={"event.amount"}) == []


def test_a_field_lookup_cannot_walk_out_of_the_extracted_fields() -> None:
    values = base.variables_for(_state(vendor="Acme"))
    assert base.render("{{field.vendor}}", values) == "Acme"
    assert base.render("{{field.__class__}}", values) == ""
    assert base.render("{{field.vendor.__class__}}", values) == ""


def test_the_email_recipient_must_be_a_real_address_not_a_field_reference() -> None:
    for recipient in ("{{field.vendor_email}}", "{{event.email}}", "attacker@evil.example, victim@corp.example", "a@b"):
        with pytest.raises(ValidationError):
            EmailSendConfig(recipient=recipient)
    assert EmailSendConfig(recipient="ops@corp.example").recipient == "ops@corp.example"


def test_the_webhook_action_has_no_url_and_refuses_one() -> None:
    assert "url" not in WebhookSendConfig.model_fields
    try:
        config = WebhookSendConfig(endpoint_id=uuid.uuid4(), url="https://attacker.example/x")  # type: ignore[call-arg]
    except ValidationError:
        return
    assert not hasattr(config, "url"), "an unexpected url field must not be accepted"


def test_the_webhook_action_can_only_name_an_endpoint_by_id() -> None:
    with pytest.raises(ValidationError):
        WebhookSendConfig(endpoint_id="https://attacker.example/x")  # type: ignore[arg-type]
