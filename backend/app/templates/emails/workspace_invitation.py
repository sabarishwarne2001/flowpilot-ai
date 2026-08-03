from __future__ import annotations

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT


def render_workspace_invitation(
    *,
    workspace_name: str,
    role_display: str,
    accept_link: str,
    expiry_str: str,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders standard subject, HTML layout, and plain-text alternatives for invitations.
    """
    subject = f"Invitation to join '{workspace_name}' workspace on {brand_name}"

    text_body = (
        f"Hello,\n\n"
        f"You have been invited to join the workspace '{workspace_name}' as a {role_display} on {brand_name}.\n\n"
        f"Click the link below to accept your invitation:\n"
        f"{accept_link}\n\n"
        f"This invitation will expire on {expiry_str}.\n\n"
        f"If you did not expect this invitation, you can safely ignore this email."
    )

    content_html = f"""<p>Hello,</p>
            <p>You have been invited to join the workspace <strong>{workspace_name}</strong> as a <strong>{role_display}</strong>.</p>
            <p>Click the button below to accept your invitation and join your team:</p>
            <div class="button-container">
                <a href="{accept_link}" class="button">Accept Invitation</a>
            </div>
            <p>If the button above does not work, copy and paste the following URL into your browser:</p>
            <p><a href="{accept_link}">{accept_link}</a></p>
            <p>This invitation will expire on <strong>{expiry_str}</strong>.</p>"""

    footer_text = f"This email was sent by {brand_name}. If you did not expect this invitation, you can safely ignore this email."

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=subject,
        brand_name=brand_name,
        content_html=content_html,
        footer_text=footer_text,
    )

    return subject, html_body, text_body