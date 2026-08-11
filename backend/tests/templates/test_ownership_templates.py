"""
ARCH-05 Step 2 — ownership template and mail-service gate.

Mirrors tests/templates/test_invitation_templates.py. No database, no Session,
no network: every template under app/templates/emails is a pure function and
these tests are what keeps that true.

The four groups fail for four different reasons:

  1. Header safety   — a tenant name with CR/LF reaching a Subject is header
                       injection, reachable by anyone who can name an
                       organization.
  2. HTML escaping   — including the <title> slot, which every ARCH-03 and
                       ARCH-04 template still passes unescaped.
  3. The §B.6 trap   — a display name reaching a mailto: href produces
                       `mailto:Jane Smith`, a dead link in the message whose
                       entire purpose is telling someone who to contact.
  4. Boundary        — templates importing models, or the mail service
                       importing a Session.
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.templates.emails.ownership_transfer_cancelled import (
    render_ownership_transfer_cancelled,
)
from app.templates.emails.ownership_transfer_declined import (
    render_ownership_transfer_declined,
)
from app.templates.emails.ownership_transfer_requested import (
    render_ownership_transfer_requested,
)
from app.templates.emails.ownership_transferred import (
    render_ownership_transferred,
)

BRAND = "FlowPilot"
SUPPORT = "support@flowpilot.test"
NOW = datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc)
EXPIRES = NOW + timedelta(days=7)

#: One string carrying every hazard at once: a header terminator, an HTML
#: element terminator that escapes <title>, and an ampersand.
HOSTILE_NAME = (
    'Acme</title><script>alert(1)</script> & Co\r\nBcc: attacker@evil.test'
)

MAILTO = re.compile(r'href="mailto:([^"]*)"')


def _all_messages(organization_name: str = "Northwind Ltd"):
    """Every message this phase can send, keyed by a readable id."""
    return {
        "requested": render_ownership_transfer_requested(
            recipient_email="target@acme.test",
            organization_name=organization_name,
            initiator_email="owner@acme.test",
            initiator_display="Jane Okafor",
            review_link="https://app.example.test/organizations/acme/ownership-transfer",
            expires_at=EXPIRES,
            brand_name=BRAND,
        ),
        "transferred_outgoing": render_ownership_transferred(
            recipient_email="owner@acme.test",
            perspective="outgoing",
            organization_name=organization_name,
            previous_owner_email="owner@acme.test",
            previous_owner_display="Jane Okafor",
            new_owner_email="target@acme.test",
            new_owner_display="Sam Whitfield",
            transferred_at=NOW,
            brand_name=BRAND,
            support_email=SUPPORT,
        ),
        "transferred_incoming": render_ownership_transferred(
            recipient_email="target@acme.test",
            perspective="incoming",
            organization_name=organization_name,
            previous_owner_email="owner@acme.test",
            previous_owner_display="Jane Okafor",
            new_owner_email="target@acme.test",
            new_owner_display="Sam Whitfield",
            transferred_at=NOW,
            brand_name=BRAND,
            support_email=SUPPORT,
        ),
        "declined": render_ownership_transfer_declined(
            recipient_email="owner@acme.test",
            organization_name=organization_name,
            target_email="target@acme.test",
            target_display="Sam Whitfield",
            declined_at=NOW,
            brand_name=BRAND,
        ),
        "cancelled": render_ownership_transfer_cancelled(
            recipient_email="target@acme.test",
            organization_name=organization_name,
            initiator_email="owner@acme.test",
            initiator_display="Jane Okafor",
            cancelled_at=NOW,
            brand_name=BRAND,
        ),
    }


MESSAGE_IDS = sorted(_all_messages().keys())


# ---------------------------------------------------------------------------
# 1. Header safety
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message_id", MESSAGE_IDS)
def test_subject_survives_a_hostile_organization_name(message_id):
    subject, _html, _text = _all_messages(HOSTILE_NAME)[message_id]
    assert "\r" not in subject
    assert "\n" not in subject
    assert len(subject) <= 160


@pytest.mark.parametrize("message_id", MESSAGE_IDS)
def test_subject_is_not_empty(message_id):
    subject, _html, _text = _all_messages()[message_id]
    assert subject.strip()


# ---------------------------------------------------------------------------
# 2. HTML escaping, including the <title> slot
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message_id", MESSAGE_IDS)
def test_hostile_name_cannot_inject_html(message_id):
    _subject, html_body, _text = _all_messages(HOSTILE_NAME)[message_id]
    assert "<script>" not in html_body
    assert "</title><script>" not in html_body


@pytest.mark.parametrize("message_id", MESSAGE_IDS)
def test_title_element_is_closed_exactly_once(message_id):
    """
    The specific breakout. A raw subject in <title>{title}</title> lets a
    tenant name containing "</title>" close the element early, and everything
    after it becomes document markup.
    """
    _subject, html_body, _text = _all_messages(HOSTILE_NAME)[message_id]
    assert html_body.count("</title>") == 1


@pytest.mark.parametrize("message_id", MESSAGE_IDS)
def test_text_body_is_present_and_not_html(message_id):
    _subject, _html, text_body = _all_messages()[message_id]
    assert text_body.strip()
    assert "<p>" not in text_body
    assert "&mdash;" not in text_body


# ---------------------------------------------------------------------------
# 3. The §B.6 trap
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message_id", MESSAGE_IDS)
def test_every_mailto_resolves_to_an_address(message_id):
    """
    §B.6 and §D R5. `mailto:Jane Okafor` is a dead link, and these messages
    exist to tell someone who to contact.
    """
    _subject, html_body, _text = _all_messages()[message_id]
    hrefs = MAILTO.findall(html_body)
    assert hrefs, "no mailto: link at all"
    for href in hrefs:
        assert "@" in href, href
        assert " " not in href, href


def test_display_names_never_reach_an_href():
    """
    The stronger form: render with a display name that could not possibly be
    an address, and assert it appears nowhere in a mailto:.
    """
    _subject, html_body, _text = render_ownership_transfer_declined(
        recipient_email="owner@acme.test",
        organization_name="Northwind Ltd",
        target_email="target@acme.test",
        target_display="Sam Whitfield",
        declined_at=NOW,
        brand_name=BRAND,
    )
    assert MAILTO.findall(html_body) == ["target@acme.test"] * len(
        MAILTO.findall(html_body)
    )
    assert "mailto:Sam" not in html_body


def test_a_null_display_name_renders_as_the_address():
    """
    §B.4: a NULL display_name renders as the email address, and the string
    "None" must never reach a surface. The caller passes the address for both
    parameters; this asserts nothing in the template second-guesses that.
    """
    _subject, html_body, text_body = render_ownership_transfer_cancelled(
        recipient_email="target@acme.test",
        organization_name="Northwind Ltd",
        initiator_email="owner@acme.test",
        initiator_display="owner@acme.test",
        cancelled_at=NOW,
        brand_name=BRAND,
    )
    assert "None" not in html_body
    assert "None" not in text_body
    assert "owner@acme.test" in text_body


# ---------------------------------------------------------------------------
# 4. Guards
# ---------------------------------------------------------------------------

def test_unknown_perspective_raises():
    with pytest.raises(ValueError):
        render_ownership_transferred(
            recipient_email="a@b.test",
            perspective="sideways",  # type: ignore[arg-type]
            organization_name="Northwind Ltd",
            previous_owner_email="a@b.test",
            previous_owner_display="A",
            new_owner_email="c@d.test",
            new_owner_display="C",
            transferred_at=NOW,
            brand_name=BRAND,
            support_email=SUPPORT,
        )


def test_the_two_perspectives_render_differently():
    """
    One template, two readers. If these ever converge, one of them is getting
    a message written for the other — a welcome to something they lost, or a
    compromise warning they cannot act on.
    """
    messages = _all_messages()
    out_subject, out_html, _ = messages["transferred_outgoing"]
    in_subject, in_html, _ = messages["transferred_incoming"]
    assert out_subject != in_subject
    assert out_html != in_html
    assert "no longer the owner" in out_subject
    assert "now the owner" in in_subject


@pytest.mark.parametrize("message_id", MESSAGE_IDS)
def test_naive_datetimes_are_refused(message_id):
    """
    format_timestamp raises on a naive value rather than labelling it UTC. An
    expiry off by the server's offset is a support ticket that looks like a
    bug in the transfer.
    """
    naive = datetime(2026, 8, 11, 9, 30)
    builders = {
        "requested": lambda: render_ownership_transfer_requested(
            recipient_email="t@a.test", organization_name="N",
            initiator_email="o@a.test", initiator_display="J",
            review_link="https://x.test", expires_at=naive, brand_name=BRAND,
        ),
        "transferred_outgoing": lambda: render_ownership_transferred(
            recipient_email="o@a.test", perspective="outgoing",
            organization_name="N", previous_owner_email="o@a.test",
            previous_owner_display="J", new_owner_email="t@a.test",
            new_owner_display="S", transferred_at=naive, brand_name=BRAND,
            support_email=SUPPORT,
        ),
        "transferred_incoming": lambda: render_ownership_transferred(
            recipient_email="t@a.test", perspective="incoming",
            organization_name="N", previous_owner_email="o@a.test",
            previous_owner_display="J", new_owner_email="t@a.test",
            new_owner_display="S", transferred_at=naive, brand_name=BRAND,
            support_email=SUPPORT,
        ),
        "declined": lambda: render_ownership_transfer_declined(
            recipient_email="o@a.test", organization_name="N",
            target_email="t@a.test", target_display="S",
            declined_at=naive, brand_name=BRAND,
        ),
        "cancelled": lambda: render_ownership_transfer_cancelled(
            recipient_email="t@a.test", organization_name="N",
            initiator_email="o@a.test", initiator_display="J",
            cancelled_at=naive, brand_name=BRAND,
        ),
    }
    with pytest.raises(ValueError):
        builders[message_id]()


def test_the_proposal_link_carries_no_token():
    """
    §B.1. The target is already authenticated, so acceptance is in-app and the
    link is a signpost rather than a credential. A token appearing here would
    mean someone reintroduced the entire class of concern this design removed.
    """
    _subject, html_body, text_body = _all_messages()["requested"]
    assert "token" not in html_body.lower()
    assert "token" not in text_body.lower()


def test_neither_completion_notice_carries_an_action_link():
    """
    password_changed's rule, applied to ownership. If the transfer was
    fraudulent, this message is being read by someone who already controls a
    session; it must hand them nothing to click.
    """
    messages = _all_messages()
    for message_id in ("transferred_outgoing", "transferred_incoming"):
        _subject, html_body, _text = messages[message_id]
        assert 'class="button"' not in html_body
        assert "http://" not in html_body
        assert "https://" not in html_body


# ---------------------------------------------------------------------------
# 5. Boundary — extends the ARCH-04 Step 1 guards unchanged
# ---------------------------------------------------------------------------

def test_ownership_templates_import_no_models_and_no_session():
    offenders = []
    for path in pathlib.Path("app/templates/emails").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "app.models" in source or "sqlalchemy" in source:
            offenders.append(str(path))
    assert not offenders, f"Templates reaching into persistence: {offenders}"


def test_ownership_mail_takes_no_session():
    source = pathlib.Path("app/services/ownership_mail.py").read_text(
        encoding="utf-8"
    )
    assert "from sqlalchemy.orm import Session" not in source
    assert "import app.crud" not in source
    assert "from app.crud" not in source
    assert "from app import crud" not in source
    assert "app.models" not in source


def test_ownership_mail_logs_no_bodies():
    """
    ARCH-03 R4. A log line carrying a subject would carry a tenant name; one
    carrying a body would carry everything.
    """
    source = pathlib.Path("app/services/ownership_mail.py").read_text(
        encoding="utf-8"
    )
    for forbidden in ("subject=%s", "html_body=%s", "text_body=%s", "link=%s"):
        assert forbidden not in source, forbidden