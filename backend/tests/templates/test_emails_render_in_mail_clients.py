"""Every transactional email is built the way mail clients can show it (campaign session 4, §A.4).

    pytest tests/templates/test_emails_render_in_mail_clients.py -q

Gmail, Outlook and Apple Mail cannot be driven from the test sandbox, so this checks the
properties each of them depends on, on the emails a customer receives (verify, reset, invitation,
the billing, limit and security notices, the trial notices) and on the operator's alert:

* a viewport and a declared colour scheme (light and dark), so phones do not zoom out and dark-mode
  clients do not invert the colours unpredictably;
* a 600-pixel table layout that Outlook for Windows (which ignores max-width) keeps;
* buttons drawn as a coloured table cell with the link inside, the only button Outlook shows as a
  button; the link itself carries its colours inline for clients that drop <style>;
* the language set, no script, and a plain-text part that carries the same link.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.templates.emails.account_notice import render_account_notice
from app.templates.emails.common import GrantLine
from app.templates.emails.operator_alert import render_operator_alert
from app.templates.emails.organization_invitation import render_organization_invitation
from app.templates.emails.password_reset import render_password_reset
from app.templates.emails.trial_notice import render_trial_notice
from app.templates.emails.verify_email import render_verify_email

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
LINK = "https://app.example.com/action#token=abc123"


def _grants() -> list:
    import inspect

    fields = inspect.signature(GrantLine).parameters
    sample = {"workspace_name": "Finance", "role_display": "Contributor", "workspace": "Finance", "role": "Contributor"}
    return [GrantLine(**{k: sample.get(k, "Finance") for k in fields})]


EMAILS = {
    "verify": lambda: render_verify_email(recipient_email="a@example.com", verify_link=LINK, expiry_str="24 hours",
                                          brand_name="FlowPilot"),
    "reset": lambda: render_password_reset(recipient_email="a@example.com", reset_link=LINK, expiry_str="1 hour",
                                           brand_name="FlowPilot"),
    "invitation": lambda: render_organization_invitation(
        invited_email="b@example.com", organization_name="Acme", inviter_email="a@example.com",
        organization_role_display="Member", grants=_grants(), accept_link=LINK,
        expires_at=NOW + timedelta(days=7), brand_name="FlowPilot"),
    "notice": lambda: render_account_notice(
        title="Your card was declined", body="Update your card to keep the workspace writable.",
        organization_name="Acme", category_label="Billing", essential=True, action_url=LINK,
        action_label="Update card", preferences_url="https://app.example.com/prefs", brand_name="FlowPilot"),
    "trial": lambda: render_trial_notice(kind="ENDING", organization_names=["Acme"], ends_at=NOW + timedelta(days=2),
                                         plans_link=LINK, brand_name="FlowPilot"),
    "operator": lambda: render_operator_alert(subject="Dead jobs", body="3 jobs failed for good.", occurrences=3,
                                              first_seen_at=NOW, last_seen_at=NOW, console_url=LINK,
                                              brand_name="FlowPilot"),
}


@pytest.mark.parametrize("name", sorted(EMAILS))
def test_the_email_suits_every_mail_client(name: str) -> None:
    subject, html_body, text_body = EMAILS[name]()
    lowered = html_body.lower()

    assert subject.strip()
    assert re.search(r'<html[^>]*\blang="en"', lowered), "no language"
    assert 'name="viewport"' in lowered, "phones zoom out without a viewport"
    assert re.search(r'name="color-scheme"[^>]*content="light dark"', lowered), "no colour scheme for dark mode"
    assert "prefers-color-scheme: dark" in lowered, "no dark-mode colours"
    assert re.search(r'<!--\[if mso\]>\s*<table[^>]*width="600"', lowered), "Outlook gets no fixed 600px column"
    assert "<script" not in lowered

    buttons = re.findall(r"<a\b[^>]*class=\"button\"[^>]*>", html_body)
    for button in buttons:
        assert "style=" in button and "color:#ffffff" in button.replace(" ", ""), f"button colours not inline: {button}"
    cells = re.findall(r'<td\b[^>]*bgcolor="#[0-9a-f]{6}"[^>]*>\s*<a\b[^>]*class="button"', html_body, re.I)
    assert len(cells) == len(buttons), f"{len(buttons)} buttons, {len(cells)} drawn as a table cell"

    assert text_body.strip(), "no plain-text part"
    assert LINK.split("#")[0] in text_body, "the plain-text part lacks the link"
