"""
Rejection notice, to the inviter.

Written to be neutral. Declining an invitation is an ordinary outcome, and copy
that reads as a failure turns a routine event into an awkward conversation. It
states the two facts that change what the inviter does next: no seat was
consumed, and the address is free to be invited again.
"""

from __future__ import annotations

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT
from app.templates.emails.common import esc, esc_attr, single_line


def render_invitation_rejected(
    *,
    invited_email: str,
    organization_name: str,
    invitations_url: str,
    brand_name: str,
) -> tuple[str, str, str]:
    """Renders the subject, HTML body, and plain-text body."""
    subject = single_line(
        f"{invited_email} declined your invitation to {organization_name}"
    )

    text_body = (
        f"Hello,\n\n"
        f"{invited_email} declined your invitation to "
        f"{organization_name}.\n\n"
        f"No seat was used and no workspace access was granted. You can send "
        f"a new invitation to the same address at any time.\n\n"
        f"Manage invitations here:\n"
        f"{invitations_url}\n"
    )

    content_html = f"""<p>Hello,</p>
            <p><strong>{esc(invited_email)}</strong> declined your invitation to
               <strong>{esc(organization_name)}</strong>.</p>
            <p>No seat was used and no workspace access was granted. You can
               send a new invitation to the same address at any time.</p>
            <div class="button-container">
                <a href="{esc_attr(invitations_url)}" class="button">Manage Invitations</a>
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