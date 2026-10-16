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
from app.templates.emails.helm import (render_briefing, render_dispute, render_document_request,
                                        render_reconciliation, render_reminder)

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
LINK = "https://app.example.com/action#token=abc123"


def _grants() -> list:
    import inspect

    fields = inspect.signature(GrantLine).parameters
    sample = {"workspace_name": "Finance", "role_display": "Contributor", "workspace": "Finance", "role": "Contributor"}
    return [GrantLine(**{k: sample.get(k, "Finance") for k in fields})]


EMAILS = {
    "verify": lambda: render_verify_email(recipient_email="a@example.com", verify_link=LINK, expiry_str="24 hours",
                                          brand_name="Servabo"),
    "reset": lambda: render_password_reset(recipient_email="a@example.com", reset_link=LINK, expiry_str="1 hour",
                                           brand_name="Servabo"),
    "invitation": lambda: render_organization_invitation(
        invited_email="b@example.com", organization_name="Acme", inviter_email="a@example.com",
        organization_role_display="Member", grants=_grants(), accept_link=LINK,
        expires_at=NOW + timedelta(days=7), brand_name="Servabo"),
    "notice": lambda: render_account_notice(
        title="Your card was declined", body="Update your card to keep the workspace writable.",
        organization_name="Acme", category_label="Billing", essential=True, action_url=LINK,
        action_label="Update card", preferences_url="https://app.example.com/prefs", brand_name="Servabo"),
    "trial": lambda: render_trial_notice(kind="ENDING", organization_names=["Acme"], ends_at=NOW + timedelta(days=2),
                                         plans_link=LINK, brand_name="Servabo"),
    "operator": lambda: render_operator_alert(subject="Dead jobs", body="3 jobs failed for good.", occurrences=3,
                                              first_seen_at=NOW, last_seen_at=NOW, console_url=LINK,
                                              brand_name="Servabo"),
    # Flagship session: Helm's emails (the Monday briefing, and what its hands send to suppliers and customers).
    "helm-briefing": lambda: render_briefing(
        brand_name="Example Cloud", organization_name="Meridian Wholesale", book_name="Main", week_of="19 Oct 2026",
        plan_lines=["Pay Konkan Packaging ₹1.2 lakh on Tue 20 Oct", "Remind Sahyadri Foods about INV-2041"],
        risk_lines=["Cash may fall below ₹10 lakh on 3 Nov (30% chance)"], value_line="₹4.9 lakh confirmed",
        waiting=3, cockpit_url=LINK, preferences_url="https://app.example.com/prefs"),
    "helm-document-request": lambda: render_document_request(
        organization_name="Meridian Wholesale", supplier_name="Deccan Freight", document_title="insurance certificate",
        valid_until="31 Oct 2026", upload_url=LINK),
    "helm-dispute": lambda: render_dispute(
        organization_name="Meridian Wholesale", supplier_name="Konkan Packaging", invoice_number="KP-1182",
        currency="INR", lines=[{"description": "Corrugated boxes", "po_number": "PO-77", "agreed_micros": 42_000_000,
                                "billed_micros": 46_000_000}], overcharge_micros=400_000_000),
    "helm-reconciliation": lambda: render_reconciliation(
        organization_name="Meridian Wholesale", supplier_name="Konkan Packaging", statement_date="30 Sep 2026",
        currency="INR", their_balance_micros=62_000 * 1_000_000, our_balance_micros=50_000 * 1_000_000,
        differences=[{"code": "NOT_IN_OUR_BOOKS", "their_micros": 12_000 * 1_000_000,
                      "evidence": [{"side": "theirs", "on": "2026-09-05", "reference": "KP-1185"}]},
                     {"code": "PAYMENT_NOT_APPLIED", "our_micros": 50_000 * 1_000_000,
                      "evidence": [{"side": "ours", "number": "KP-1182", "paid_on": "2026-09-20",
                                    "paid_reference": "UTR 4471"}]}]),
    "helm-reminder": lambda: render_reminder(
        organization_name="Meridian Wholesale", customer_name="Sahyadri Foods", invoice_number="INV-2041",
        currency="INR", amount_micros=180_000 * 1_000_000, due_date="5 Oct 2026", days_overdue=10, level=2),
}

#: Emails a supplier or customer receives about one invoice or their statement: no link to follow, so no button.
LINKLESS = {"helm-dispute", "helm-reconciliation", "helm-reminder"}


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
    if name in LINKLESS:
        assert not buttons, "an email with nothing to follow has no button"
        return
    assert LINK.split("#")[0] in text_body, "the plain-text part lacks the link"


# Website and payments session (Part A8): mail sent as Servabo carries the Servabo mark; mail sent
# under an organization's own brand never does.


def test_servabo_mail_shows_the_mark_beside_the_name() -> None:
    _, html, _ = EMAILS["verify"]()
    header = html.split('<div class="content"', 1)[0]
    assert re.search(r'<span class="mark" aria-hidden="true" style="[^"]*background-color:#1555e0[^"]*">S</span>', header)
    assert ">Servabo</span>" in header
    assert "<img" not in header, "no remote image: clients hold them, and some emails must carry no address"


def test_white_label_mail_has_no_servabo_mark() -> None:
    _, html, _ = EMAILS["helm-briefing"]()
    assert 'class="mark"' not in html
    assert ">Example Cloud</span>" in html


# Website and payments session (Part B7): Servabo's own mail links its terms, privacy policy and
# refund policy on the website, in emails that already carry a link; never under another brand,
# and never in an email that must stay free of addresses.


def test_servabo_mail_with_a_link_ends_with_the_legal_pages() -> None:
    from app.core.config import settings

    _, html, _ = EMAILS["verify"]()
    footer = html.split('<div class="footer"', 1)[1]
    for path, label in (("terms", "Terms"), ("privacy", "Privacy"), ("refunds", "Refunds")):
        assert f'href="{settings.WEBSITE_URL}/legal/{path}"' in footer and f">{label}</a>" in footer, path
    assert settings.WEBSITE_URL == "https://servabo.app"


@pytest.mark.parametrize("name", sorted(LINKLESS | {"helm-briefing"}))
def test_linkless_or_white_label_mail_has_no_legal_links(name: str) -> None:
    _, html, _ = EMAILS[name]()
    assert 'class="legal"' not in html and "/legal/" not in html
