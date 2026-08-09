"""
The invitation itself: FlowPilot writing to someone who is not yet a member of
anything, on behalf of a tenant they cannot yet see.

Replaces workspace_invitation.py, which could describe exactly one workspace at
exactly one role because that was all an invitation could carry. This one takes
a list, and the list may be empty.

The zero-grant branch is not an edge case to defend against -- it is how a
BILLING manager is onboarded (B.1), and getting its copy right is most of the
reason the template was rewritten rather than extended. "Workspaces: (none)"
would read as a bug to the one recipient for whom it is the correct outcome.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT
from app.templates.emails.common import (
    GrantLine,
    esc,
    esc_attr,
    format_timestamp,
    render_grant_lines_text,
    render_grant_table,
    single_line,
)


def render_organization_invitation(
    *,
    invited_email: str,
    organization_name: str,
    inviter_display: str,
    organization_role_display: str,
    grants: Sequence[GrantLine],
    accept_link: str,
    expires_at: datetime,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body of an invitation.

    Args:
        invited_email: The address the invitation was issued to. Stated in the
            body because acceptance requires the actor's session email to
            match it (ARCH-03 B.4 Option 2, preserved by ARCH-04 B.5). A
            recipient who signs in with a different address gets a rejection
            they cannot interpret unless the email warned them first.
        organization_name: Tenant-supplied. Escaped, and sanitised before it
            reaches the subject.
        inviter_display: The inviter's address. Also used as Reply-To by the
            caller (D1.2).
        organization_role_display: ADMIN, BILLING or MEMBER. OWNER is not
            invitable (B.4) and this template will never see it.
        grants: Workspace grants attached to the invitation. May be empty.
        accept_link: Fully built by build_invitation_accept_link. This template
            must never construct a link itself, or the B.10 shape would have
            two definitions.
        expires_at: Aware datetime. format_timestamp raises on a naive one.

    Returns:
        (subject, html_body, text_body)
    """
    subject = single_line(f"You have been invited to join {organization_name}")
    expiry_str = format_timestamp(expires_at)

    # ---- plain text -----------------------------------------------------
    if grants:
        grants_text = (
            "This invitation also gives you access to these workspaces:\n"
            f"{render_grant_lines_text(grants)}\n\n"
        )
    else:
        grants_text = (
            "This invitation does not include access to any workspaces. An "
            "administrator can add you to workspaces at any time after you "
            "join.\n\n"
        )

    text_body = (
        f"Hello,\n\n"
        f"{inviter_display} has invited you to join {organization_name} on "
        f"{brand_name}.\n\n"
        f"Your role in the organization will be "
        f"{organization_role_display}.\n\n"
        f"{grants_text}"
        f"Open the link below to accept:\n"
        f"{accept_link}\n\n"
        f"This invitation was sent to {invited_email}. Sign in with that "
        f"address to accept it -- signing in with a different address will "
        f"not work.\n\n"
        f"This link expires on {expiry_str}.\n\n"
        f"If you were not expecting this, ignore this email. Nothing happens "
        f"until the link is used."
    )

    # ---- html -----------------------------------------------------------
    safe_link = esc_attr(accept_link)

    if grants:
        grants_html = (
            "<p>This invitation also gives you access to these workspaces:</p>"
            f"{render_grant_table(grants)}"
        )
    else:
        grants_html = (
            "<p>This invitation does not include access to any workspaces. An "
            "administrator can add you to workspaces at any time after you "
            "join.</p>"
        )

    content_html = f"""<p>Hello,</p>
            <p><strong>{esc(inviter_display)}</strong> has invited you to join
               <strong>{esc(organization_name)}</strong> on {esc(brand_name)}.</p>
            <p>Your role in the organization will be
               <strong>{esc(organization_role_display)}</strong>.</p>
            {grants_html}
            <div class="button-container">
                <a href="{safe_link}" class="button">Accept Invitation</a>
            </div>
            <p>If the button does not work, copy this URL into your browser:</p>
            <p><a href="{safe_link}">{esc(accept_link)}</a></p>
            <p>This invitation was sent to <strong>{esc(invited_email)}</strong>.
               Sign in with that address to accept it &mdash; signing in with a
               different address will not work.</p>
            <p>This link expires on <strong>{esc(expiry_str)}</strong>.</p>"""

    footer_text = (
        f"This invitation was sent by {brand_name} on behalf of "
        f"{esc(organization_name)}. If you were not expecting it you can ignore "
        f"it -- nothing happens until the link is used."
    )

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=esc(subject),
        brand_name=brand_name,
        content_html=content_html,
        footer_text=footer_text,
    )

    return subject, html_body, text_body