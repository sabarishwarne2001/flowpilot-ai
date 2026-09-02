"""
Revocation notice, to the invitee.

One of the two ARCH-04 messages that travels toward the recipient rather than
the sender. B.7: the inviter performed the revocation and does not need
telling; the recipient is holding a link that has stopped working and does.

Read it assuming the recipient is not who we think. The address was never
verified -- that is precisely what accepting would have proved -- so this
message may land in a mailbox belonging to someone else, or in one that was
mistyped. It therefore contains no link of any kind, and reveals nothing beyond
what the original invitation already told the same mailbox: the organization's
name and the inviter's address.
"""

from __future__ import annotations

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT
from app.templates.emails.common import esc, esc_attr, single_line


def render_invitation_revoked(
    *,
    invited_email: str,
    organization_name: str,
    inviter_display: str | None = None,
    inviter_email: str | None = None,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body.

    Args:
        invited_email: Target recipient email address.
        organization_name: Name of the organization.
        inviter_display: The inviter's display name for prose. Defaults to
            `inviter_email` when omitted.
        inviter_email: The inviter's email address for the mailto: href.
            Defaults to `inviter_display` for backward compatibility.
        brand_name: Platform brand name.
    """
    email_for_href = inviter_email or inviter_display or ""
    display = inviter_display or inviter_email or ""

    subject = single_line(
        f"Your invitation to {organization_name} was withdrawn"
    )

    text_body = (
        f"Hello,\n\n"
        f"The invitation sent to {invited_email} for {organization_name} on "
        f"{brand_name} has been withdrawn. The link in that email no longer "
        f"works.\n\n"
        f"If you were expecting to join, contact {display} -- they "
        f"can send a new invitation.\n\n"
        f"No action is needed otherwise."
    )

    content_html = f"""<p>Hello,</p>
            <p>The invitation sent to <strong>{esc(invited_email)}</strong> for
               <strong>{esc(organization_name)}</strong> has been withdrawn.
               The link in that email no longer works.</p>
            <p>If you were expecting to join, contact
               <a href="mailto:{esc_attr(email_for_href)}">{esc(display)}</a>
               &mdash; they can send a new invitation.</p>
            <p>No action is needed otherwise.</p>"""

    footer_text = (
        f"This notice was sent by {brand_name} because an invitation to this "
        f"address was withdrawn."
    )

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=subject,
        brand_name=brand_name,
        content_html=content_html,
        footer_text=footer_text,
    )

    return subject, html_body, text_body
