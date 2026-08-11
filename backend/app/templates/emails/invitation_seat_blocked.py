"""
Seat-exhaustion notice, to the inviter. ARCH-04 B.8.

The seat count is checked twice: at issuance, so a full organization learns
before an email goes out, and again at acceptance, because between the two
someone else may have taken the last seat. This message exists for the second
case.

B.8 requires that failure to be graceful, and the shape of the failure is what
this copy has to convey: the invitation is still PENDING, the token was NOT
consumed, and the recipient's link still works. Nothing needs reissuing. The
inviter frees a seat or raises the limit, tells the invitee to click again, and
it succeeds. Copy that implied the invitation was lost would prompt a
re-invitation, which under the B.9 partial unique index revokes and replaces
the live one -- turning a recoverable state into a dead link in the invitee's
mailbox, which is exactly the outcome B.8 was written to prevent.
"""

from __future__ import annotations

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT
from app.templates.emails.common import esc, esc_attr, single_line


def render_invitation_seat_blocked(
    *,
    invited_email: str,
    organization_name: str,
    seat_limit: int | None,
    members_url: str,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body.

    Args:
        seat_limit: The organization's limit, or None for unlimited (B.8).
            None here means the check failed for a reason other than the
            limit, so the sentence naming a number is omitted rather than
            rendered as "None".
    """
    subject = single_line(
        f"{invited_email} could not join {organization_name} -- no seats "
        f"available"
    )

    if seat_limit is not None:
        limit_text = (
            f"{organization_name} has reached its limit of {seat_limit} "
            f"seats.\n\n"
        )
        limit_html = (
            f"<p><strong>{esc(organization_name)}</strong> has reached its "
            f"limit of <strong>{esc(str(seat_limit))}</strong> seats.</p>"
        )
    else:
        limit_text = f"{organization_name} has no seats available.\n\n"
        limit_html = (
            f"<p><strong>{esc(organization_name)}</strong> has no seats "
            f"available.</p>"
        )

    text_body = (
        f"Hello,\n\n"
        f"{invited_email} tried to accept your invitation to "
        f"{organization_name} and could not.\n\n"
        f"{limit_text}"
        f"Their invitation is still valid and their link still works. Free a "
        f"seat or raise the limit, then ask them to open the link again -- "
        f"there is no need to send a new invitation.\n\n"
        f"Review members here:\n"
        f"{members_url}\n"
    )

    content_html = f"""<p>Hello,</p>
            <p><strong>{esc(invited_email)}</strong> tried to accept your
               invitation to <strong>{esc(organization_name)}</strong> and
               could not.</p>
            {limit_html}
            <p>Their invitation is still valid and their link still works.
               Free a seat or raise the limit, then ask them to open the link
               again &mdash; there is no need to send a new invitation.</p>
            <div class="button-container">
                <a href="{esc_attr(members_url)}" class="button">Review Members</a>
            </div>"""

    footer_text = (
        f"You are receiving this from {brand_name} because you sent this "
        f"invitation."
    )

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=esc(subject),
        brand_name=esc(brand_name),
        content_html=content_html,
        footer_text=esc(footer_text),
    )

    return subject, html_body, text_body