"""
Expiry digest, to the inviter. One message per inviter per sweep run.

B.7: a sweeper that mails per expired row will one day mail four hundred
messages at 03:17. Grouping is by inviter alone rather than by
(inviter, organization) -- a person who invited into three organizations gets
one message with the organization named on each line, not three messages. Every
line names an organization the recipient is already a member of, so grouping
this way discloses nothing.

Rows are capped at MAX_DIGEST_ROWS with the remainder summarised. An unbounded
digest is the same failure as per-row mail wearing a different hat.
"""

from __future__ import annotations

from typing import Sequence

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT
from app.templates.emails.common import (
    ExpiredInvitationLine,
    esc,
    esc_attr,
    pluralize,
    render_table,
    single_line,
    truncate_digest,
)


def render_invitation_expiry_digest(
    *,
    lines: Sequence[ExpiredInvitationLine],
    invitations_url: str,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body.

    Args:
        lines: Every invitation this inviter issued that lapsed in this run.
            Must be non-empty; the sweeper does not send an empty digest, and
            this raises rather than rendering "0 invitations expired".
    """
    if not lines:
        raise ValueError(
            "Refusing to render an empty expiry digest. The sweeper must skip "
            "inviters with nothing to report (B.7)."
        )

    total = len(lines)
    shown, overflow = truncate_digest(lines)
    noun = pluralize(total, "invitation")

    subject = single_line(f"{total} {noun} expired without being accepted")

    text_rows = "\n".join(
        f"  - {line.invited_email} -- {line.organization_name} "
        f"(expired {line.expired_at_display})"
        for line in shown
    )
    overflow_sentence = f"\n  ... and {overflow} more." if overflow else ""

    text_body = (
        f"Hello,\n\n"
        f"{total} {noun} you sent expired without being accepted:\n\n"
        f"{text_rows}{overflow_sentence}\n\n"
        f"These addresses are free to invite again.\n\n"
        f"Manage invitations here:\n"
        f"{invitations_url}\n"
    )

    table_html = render_table(
        [
            (line.invited_email, line.organization_name, line.expired_at_display)
            for line in shown
        ],
        headers=("Invited", "Organization", "Expired"),
    )
    overflow_html = f"<p>&hellip; and {esc(str(overflow))} more.</p>" if overflow else ""

    content_html = f"""<p>Hello,</p>
            <p><strong>{esc(str(total))}</strong> {esc(noun)} you sent expired
               without being accepted.</p>
            {table_html}
            {overflow_html}
            <p>These addresses are free to invite again.</p>
            <div class="button-container">
                <a href="{esc_attr(invitations_url)}" class="button">Manage Invitations</a>
            </div>"""

    footer_text = (
        f"This is a summary from {brand_name} of invitations you sent that "
        f"expired. It is sent once per sweep, never once per invitation."
    )

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=esc(subject),
        brand_name=esc(brand_name),
        content_html=content_html,
        footer_text=esc(footer_text),
    )

    return subject, html_body, text_body
