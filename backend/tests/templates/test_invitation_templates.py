"""
ARCH-04 Step 1 -- template rendering.

Pure functions in, strings out. If any test here needs a database, a boundary
Step 1 exists to hold has been crossed.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from app.core.links import (
    build_invitation_accept_link,
    build_legacy_invitation_accept_link,
)
from app.templates.emails.common import (
    MAX_DIGEST_ROWS,
    ExpiredInvitationLine,
    GrantLine,
    format_timestamp,
    single_line,
)
from app.templates.emails.invitation_accepted import render_invitation_accepted
from app.templates.emails.invitation_expiry_digest import (
    render_invitation_expiry_digest,
)
from app.templates.emails.invitation_rejected import render_invitation_rejected
from app.templates.emails.invitation_revoked import render_invitation_revoked
from app.templates.emails.invitation_seat_blocked import (
    render_invitation_seat_blocked,
)
from app.templates.emails.organization_invitation import (
    render_organization_invitation,
)

BRAND = "FlowPilot AI"
EXPIRES = datetime.now(timezone.utc) + timedelta(hours=72)
LINK = "https://app.example.com/invitations/accept#token=abc123"
MEMBERS = "https://app.example.com/members"
INVITES = "https://app.example.com/invitations"

XSS = '<script>alert("x")</script>'
INJECTION = "Acme\r\nBcc: attacker@example.com"


def _invitation(**overrides):
    kwargs = dict(
        invited_email="new@example.com",
        organization_name="Acme Ltd",
        inviter_display="owner@acme.example",
        organization_role_display="MEMBER",
        grants=[GrantLine("Operations", "EDITOR")],
        accept_link=LINK,
        expires_at=EXPIRES,
        brand_name=BRAND,
    )
    kwargs.update(overrides)
    return render_organization_invitation(**kwargs)


def _all_subjects(org_name: str) -> list[str]:
    """Every subject line in the phase, for the header-safety sweep."""
    return [
        _invitation(organization_name=org_name)[0],
        render_invitation_accepted(
            invited_email="a@b.example",
            organization_name=org_name,
            organization_role_display="MEMBER",
            provisioned_grants=[],
            skipped_grant_count=0,
            members_url=MEMBERS,
            brand_name=BRAND,
        )[0],
        render_invitation_rejected(
            invited_email="a@b.example",
            organization_name=org_name,
            invitations_url=INVITES,
            brand_name=BRAND,
        )[0],
        render_invitation_revoked(
            invited_email="a@b.example",
            organization_name=org_name,
            inviter_display="owner@acme.example",
            brand_name=BRAND,
        )[0],
        render_invitation_seat_blocked(
            invited_email="a@b.example",
            organization_name=org_name,
            seat_limit=10,
            members_url=MEMBERS,
            brand_name=BRAND,
        )[0],
        render_invitation_expiry_digest(
            lines=[ExpiredInvitationLine("a@b.example", org_name, "2026-01-01")],
            invitations_url=INVITES,
            brand_name=BRAND,
        )[0],
    ]


# ---------------------------------------------------------------------------
# Header safety -- D1.4
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw", [INJECTION, "Acme\nX-Evil: 1", "Acme\x00Ltd", "Acme\x7fLtd"]
)
def test_no_subject_carries_a_control_character(raw):
    for subject in _all_subjects(raw):
        assert "\r" not in subject
        assert "\n" not in subject
        assert "\x00" not in subject


def test_single_line_bounds_length():
    assert len(single_line("A" * 500)) <= 160


# ---------------------------------------------------------------------------
# HTML escaping
# ---------------------------------------------------------------------------

def test_tenant_names_are_escaped_in_html():
    _, html_body, _ = _invitation(
        organization_name=XSS, grants=[GrantLine(XSS, "EDITOR")]
    )
    assert XSS not in html_body
    assert "&lt;script&gt;" in html_body


def test_accept_link_is_escaped_as_an_attribute():
    _, html_body, _ = _invitation(
        accept_link='https://x.example/a#token=1"onload="alert(1)'
    )
    assert 'onload="alert(1)' not in html_body


# ---------------------------------------------------------------------------
# Zero-grant branch -- the BILLING case, B.1
# ---------------------------------------------------------------------------

def test_zero_grant_invitation_explains_itself():
    _, html_body, text_body = _invitation(grants=[])
    assert "does not include access to any workspaces" in text_body
    assert "does not include access to any workspaces" in html_body
    assert "<table" not in html_body           # no empty table rendered
    assert "(none)" not in text_body


def test_grants_are_listed_when_present():
    _, html_body, text_body = _invitation(
        grants=[GrantLine("Ops", "EDITOR"), GrantLine("Finance", "VIEWER")]
    )
    assert "Finance" in text_body and "Finance" in html_body
    assert "<table" in html_body


# ---------------------------------------------------------------------------
# Identity guidance -- ARCH-03 B.4 Option 2 depends on the email match
# ---------------------------------------------------------------------------

def test_invitation_states_which_address_must_sign_in():
    _, html_body, text_body = _invitation(invited_email="target@example.com")
    assert "target@example.com" in text_body
    assert "target@example.com" in html_body


def test_accept_link_appears_in_both_alternatives():
    _, html_body, text_body = _invitation()
    assert LINK in text_body
    assert LINK in html_body


# ---------------------------------------------------------------------------
# Digest -- singular, plural, overflow, empty
# ---------------------------------------------------------------------------

def _line(n: int) -> ExpiredInvitationLine:
    return ExpiredInvitationLine(
        f"p{n}@example.com", "Acme Ltd", "2026-01-04 03:17 UTC"
    )


def test_digest_renders_singular():
    subject, _, text_body = render_invitation_expiry_digest(
        lines=[_line(1)], invitations_url=INVITES, brand_name=BRAND
    )
    assert subject.startswith("1 invitation ")
    assert "1 invitations" not in text_body


def test_digest_renders_plural():
    subject, _, _ = render_invitation_expiry_digest(
        lines=[_line(1), _line(2)], invitations_url=INVITES, brand_name=BRAND
    )
    assert subject.startswith("2 invitations ")


def test_digest_caps_rows_and_reports_overflow():
    lines = [_line(n) for n in range(MAX_DIGEST_ROWS + 7)]
    _, html_body, text_body = render_invitation_expiry_digest(
        lines=lines, invitations_url=INVITES, brand_name=BRAND
    )
    assert "and 7 more" in text_body
    assert text_body.count("@example.com") <= MAX_DIGEST_ROWS
    assert "7 more" in html_body


def test_digest_refuses_to_render_empty():
    with pytest.raises(ValueError):
        render_invitation_expiry_digest(
            lines=[], invitations_url=INVITES, brand_name=BRAND
        )


# ---------------------------------------------------------------------------
# Acceptance notice reports what was provisioned -- B.2 / R8
# ---------------------------------------------------------------------------

def test_acceptance_reports_skipped_grants():
    _, html_body, text_body = render_invitation_accepted(
        invited_email="new@example.com",
        organization_name="Acme Ltd",
        organization_role_display="MEMBER",
        provisioned_grants=[GrantLine("Ops", "EDITOR")],
        skipped_grant_count=2,
        members_url=MEMBERS,
        brand_name=BRAND,
    )
    assert "2 workspaces on this invitation no longer exist" in text_body
    assert "no longer exist" in html_body


def test_acceptance_of_zero_grant_invitation_reads_correctly():
    _, _, text_body = render_invitation_accepted(
        invited_email="cfo@example.com",
        organization_name="Acme Ltd",
        organization_role_display="BILLING",
        provisioned_grants=[],
        skipped_grant_count=0,
        members_url=MEMBERS,
        brand_name=BRAND,
    )
    assert "not granted access to any workspace" in text_body
    assert "Their seat in the organization is active" in text_body


# ---------------------------------------------------------------------------
# Seat-blocked notice -- B.8
# ---------------------------------------------------------------------------

def test_seat_blocked_promises_the_link_still_works():
    _, html_body, text_body = render_invitation_seat_blocked(
        invited_email="new@example.com",
        organization_name="Acme Ltd",
        seat_limit=25,
        members_url=MEMBERS,
        brand_name=BRAND,
    )
    assert "still works" in text_body and "still works" in html_body
    assert "no need to send a new invitation" in text_body
    assert "25" in text_body


def test_seat_blocked_omits_the_number_when_the_limit_is_unset():
    _, _, text_body = render_invitation_seat_blocked(
        invited_email="new@example.com",
        organization_name="Acme Ltd",
        seat_limit=None,
        members_url=MEMBERS,
        brand_name=BRAND,
    )
    assert "None" not in text_body
    assert "no seats available" in text_body


# ---------------------------------------------------------------------------
# Revocation reveals nothing and links nowhere -- B.7
# ---------------------------------------------------------------------------

def test_revocation_notice_contains_no_http_link():
    _, html_body, text_body = render_invitation_revoked(
        invited_email="a@b.example",
        organization_name="Acme Ltd",
        inviter_display="owner@acme.example",
        brand_name=BRAND,
    )
    assert "http://" not in text_body and "https://" not in text_body
    assert "http://" not in html_body and "https://" not in html_body
    assert "mailto:owner@acme.example" in html_body


# ---------------------------------------------------------------------------
# Links -- B.10
# ---------------------------------------------------------------------------

def test_accept_link_uses_a_fragment():
    link = build_invitation_accept_link(
        "tok", frontend_url="https://app.example.com/"
    )
    assert link == "https://app.example.com/invitations/accept#token=tok"
    assert "?token=" not in link


def test_legacy_builder_still_produces_the_query_form():
    """Retained for one release for the in-flight invitation. B.10."""
    link = build_legacy_invitation_accept_link(
        "tok", frontend_url="https://app.example.com"
    )
    assert link.endswith("/invitations/accept?token=tok")


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError):
        format_timestamp(datetime(2026, 1, 1, 12, 0))


# ---------------------------------------------------------------------------
# Boundary guards -- the structural point of Step 1
# ---------------------------------------------------------------------------

def test_new_email_templates_import_no_models_and_no_session():
    """
    workspace_invitation.py is excluded: it predates this rule and is dropped
    at Step 5. Every template written in ARCH-04 is held to it.
    """
    legacy = {"workspace_invitation.py"}
    offenders = []
    for path in pathlib.Path("app/templates/emails").rglob("*.py"):
        if path.name in legacy:
            continue
        source = path.read_text(encoding="utf-8")
        if "app.models" in source or "sqlalchemy" in source:
            offenders.append(str(path))
    assert not offenders, f"Templates reaching into persistence: {offenders}"


def test_invitation_mail_takes_no_session():
    source = pathlib.Path("app/services/invitation_mail.py").read_text(
        encoding="utf-8"
    )
    assert "from sqlalchemy.orm import Session" not in source
    assert "import app.crud" not in source
    assert "from app.crud" not in source
    assert "from app import crud" not in source


def test_fragment_link_builder_has_no_live_call_site_yet():
    """
    D1.5 / R6. Until Step 7 the frontend cannot read a fragment, so nothing in
    app/ may call the new builder. Delete this test in Step 7.
    """
    callers = [
        str(path)
        for path in pathlib.Path("app").rglob("*.py")
        if path.name not in ("links.py", "organization_invitation_service.py")
        and "build_invitation_accept_link(" in path.read_text(encoding="utf-8")
    ]
    assert not callers, f"Fragment link used before Step 7: {callers}"