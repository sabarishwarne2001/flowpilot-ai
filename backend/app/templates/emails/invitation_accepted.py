"""
Acceptance notice, to the inviter.

Reports the grants that were actually provisioned, not the grants the
invitation was issued with. Under B.2 a workspace deleted between issuance and
acceptance takes its grant with it by cascade, and acceptance provisions fewer
than it was issued with. That is correct behaviour, and the person who sent the
invitation is the one who needs to know it happened -- otherwise a colleague is
missing a workspace and nobody can account for why.

Carries no link that grants anything. The members link requires a session.
"""

from __future__ import annotations

from typing import Sequence

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT
from app.templates.emails.common import (
    GrantLine,
    esc,
    esc_attr,
    render_grant_lines_text,
    render_grant_table,
    single_line,
)


def render_invitation_accepted(
    *,
    invited_email: str,
    organization_name: str,
    organization_role_display: str,
    provisioned_grants: Sequence[GrantLine],
    skipped_grant_count: int,
    members_url: str,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body.

    Args:
        provisioned_grants: What acceptance actually created.
        skipped_grant_count: Grants on the invitation that no longer resolved
            to a live workspace. Normally zero; when it is not, saying so is
            the entire value of this message (B.2, R8).
        members_url: Deep link to the organization member directory. Requires
            authentication; it is a destination, not a credential.
    """
    subject = single_line(f"{invited_email} joined {organization_name}")

    if provisioned_grants:
        grants_text = (
            "Workspaces they now have access to:\n"
            f"{render_grant_lines_text(provisioned_grants)}\n\n"
        )
        grants_html = (
            "<p>Workspaces they now have access to:</p>"
            f"{render_grant_table(provisioned_grants)}"
        )
    else:
        grants_text = (
            "They were not granted access to any workspace. Their seat in the "
            "organization is active.\n\n"
        )
        grants_html = (
            "<p>They were not granted access to any workspace. Their seat in "
            "the organization is active.</p>"
        )

    if skipped_grant_count:
        noun = "workspace" if skipped_grant_count == 1 else "workspaces"
        verb = "no longer exists" if skipped_grant_count == 1 else "no longer exist"
        skipped_sentence = (
            f"{skipped_grant_count} {noun} on this invitation {verb}, so no "
            f"access was granted there."
        )
        grants_text += f"{skipped_sentence}\n\n"
        grants_html += f"<p><strong>{esc(skipped_sentence)}</strong></p>"

    text_body = (
        f"Hello,\n\n"
        f"{invited_email} accepted your invitation to {organization_name} and "
        f"joined as {organization_role_display}.\n\n"
        f"{grants_text}"
        f"You can review the member directory here:\n"
        f"{members_url}\n"
    )

    content_html = f"""<p>Hello,</p>
            <p><strong>{esc(invited_email)}</strong> accepted your invitation to
               <strong>{esc(organization_name)}</strong> and joined as
               <strong>{esc(organization_role_display)}</strong>.</p>
            {grants_html}
            <div class="button-container">
                <a href="{esc_attr(members_url)}" class="button">View Members</a>
            </div>"""

    footer_text = (
        f"You are receiving this from {brand_name} because you sent this "
        f"invitation."
    )

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=subject,
        brand_name=brand_name,
        content_html=content_html,
        footer_text=footer_text,
    )

    return subject, html_body, text_body