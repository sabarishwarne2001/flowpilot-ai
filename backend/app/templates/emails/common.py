"""
Shared rendering helpers for outbound transactional email.

Everything under app/templates/emails/ is a pure function: no Session, no ORM
import, no network, no clock. That boundary is load-bearing rather than tidy.
ARCH-04 Step 1 lands before the models it describes exist (Step 2), so a
template importing OrganizationInvitation could not be written yet, let alone
tested. Holding the line means the six messages are unit-testable with no
database, and the Step 8 sweeper can import them from a plain script context.

Two hazards these helpers close.

1. Tenant text in HTML. organization_name, workspace_name and inviter
   addresses are free text supplied by users of the product. Every one is
   escaped before interpolation; attribute values additionally with
   quote=True. The template this replaces escaped nothing.

2. Tenant text in a Subject header. ARCH-04 is the first phase to place an
   organization name in a subject line. A name containing CR or LF is header
   injection: EmailMessage will either raise at serialization -- a 500 on the
   invite endpoint, reachable by anyone who can name an organization -- or, on
   a permissive policy, emit an attacker-chosen Bcc. html.escape does nothing
   about this; it is an HTML function and a carriage return is not HTML.
   single_line() is the guard, and every subject goes through it.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

#: Characters that terminate or fold a header. Removed rather than replaced
#: with a space: a value containing one is malformed, not badly spaced.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

_WHITESPACE_RUN = re.compile(r"\s+")

#: Subjects past this are folded by every MTA and truncated by every client.
#: Bounding it here keeps the truncation ours, and predictable.
MAX_SUBJECT_LENGTH = 160

#: A sweep that expires four hundred invitations from one inviter must not
#: produce a four-hundred-row email. Overflow is summarised (B.7).
MAX_DIGEST_ROWS = 50

_TABLE_STYLE = "width:100%;border-collapse:collapse;margin:18px 0;"
_HEAD_STYLE = (
    "padding:8px 12px;text-align:left;font-size:11px;letter-spacing:.06em;"
    "text-transform:uppercase;color:#666666;border-bottom:2px solid #e1e1e1;"
)
_CELL_STYLE = (
    "padding:10px 12px;font-size:14px;color:#333333;"
    "border-bottom:1px solid #eeeeee;"
)


# ==========================================================================
# Value objects
# ==========================================================================

@dataclass(frozen=True)
class GrantLine:
    """
    One workspace grant as it appears in a message.

    Deliberately not InvitationWorkspaceGrant. That model arrives in Step 2,
    and binding the template to it would mean the copy could not be reviewed
    or sent until the schema landed. It also keeps the acceptance notice
    honest: what it reports is the grants that were *provisioned*, which under
    B.2 and R8 may be fewer than the grants the invitation was issued with.
    """

    workspace_name: str
    role_display: str


@dataclass(frozen=True)
class ExpiredInvitationLine:
    """One lapsed invitation as it appears in the digest."""

    invited_email: str
    organization_name: str
    expired_at_display: str


# ==========================================================================
# Sanitation
# ==========================================================================

def single_line(value: str, *, max_length: int = MAX_SUBJECT_LENGTH) -> str:
    """
    Makes a caller-supplied string safe to place in an email header.

    Strips control characters, collapses whitespace runs, trims, and bounds
    the length. Call this on every value interpolated into a subject line.
    """
    collapsed = _CONTROL_CHARS.sub("", value or "")
    collapsed = _WHITESPACE_RUN.sub(" ", collapsed).strip()

    if len(collapsed) > max_length:
        collapsed = collapsed[: max_length - 1].rstrip() + "\u2026"

    return collapsed


def esc(value: str) -> str:
    """Escapes a value for interpolation into HTML text content."""
    return html.escape(value or "")


def esc_attr(value: str) -> str:
    """Escapes a value for interpolation into an HTML attribute value."""
    return html.escape(value or "", quote=True)


def format_timestamp(value: datetime) -> str:
    """
    Renders an aware datetime for display, in UTC.

    Raises on a naive value rather than assuming one. Formatting a naive
    datetime with a 'UTC' suffix asserts a timezone the value does not carry,
    and an invitation expiry off by the server's offset is a support ticket
    that looks like a bug in the token.
    """
    if value.tzinfo is None:
        raise ValueError(
            "Naive datetime passed to an email template. Labelling it UTC "
            "would assert a timezone the value does not carry."
        )

    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def pluralize(count: int, singular: str, plural: str | None = None) -> str:
    """Returns the correct noun form. The digest does render for count == 1."""
    if count == 1:
        return singular
    return plural or f"{singular}s"


# ==========================================================================
# Table rendering
# ==========================================================================

def render_table(
    rows: Sequence[Sequence[str]],
    *,
    headers: Sequence[str] | None = None,
) -> str:
    """
    Renders a data table with every cell escaped.

    Inline styles rather than classes on BASE_EMAIL_HTML_LAYOUT: several
    clients strip <style> blocks, and adding rules to the shared layout would
    touch a file four already-shipped messages depend on.
    """
    if not rows:
        return ""

    head_html = ""
    if headers:
        cells = "".join(
            f'<th style="{_HEAD_STYLE}">{esc(header)}</th>' for header in headers
        )
        head_html = f"<thead><tr>{cells}</tr></thead>"

    body_rows = "".join(
        "<tr>"
        + "".join(f'<td style="{_CELL_STYLE}">{esc(cell)}</td>' for cell in row)
        + "</tr>"
        for row in rows
    )

    return (
        f'<table cellpadding="0" cellspacing="0" style="{_TABLE_STYLE}">'
        f"{head_html}<tbody>{body_rows}</tbody></table>"
    )


def render_grant_table(grants: Sequence[GrantLine]) -> str:
    """Workspace grants as an HTML table. Empty string for zero grants."""
    return render_table(
        [(grant.workspace_name, grant.role_display) for grant in grants],
        headers=("Workspace", "Role"),
    )


def render_grant_lines_text(grants: Sequence[GrantLine]) -> str:
    """Workspace grants for the plain-text alternative."""
    return "\n".join(
        f"  - {grant.workspace_name} ({grant.role_display})" for grant in grants
    )


def truncate_digest(
    lines: Sequence[ExpiredInvitationLine],
) -> tuple[list[ExpiredInvitationLine], int]:
    """
    Bounds a digest to MAX_DIGEST_ROWS, reporting how many were withheld.

    Returns (shown, overflow_count).
    """
    shown = list(lines[:MAX_DIGEST_ROWS])
    return shown, max(0, len(lines) - len(shown))