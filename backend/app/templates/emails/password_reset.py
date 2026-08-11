"""
Password reset message.

Token delivery follows the same fragment rule as verification (ARCH-03 §B.9).

The copy is written so that it reads correctly to someone who did *not* request
the reset — that person is the one who most needs to understand what is
happening, and the message is their first signal that someone is trying to take
their account.
"""

from __future__ import annotations

import html

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT


def render_password_reset(
    *,
    recipient_email: str,
    reset_link: str,
    expiry_str: str,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body for a password reset.

    Args:
        recipient_email: The account address the reset was requested for.
        reset_link: Fully-qualified frontend URL with the token in the
            fragment, e.g. https://app.flowpilot.ai/reset-password#token=...
        expiry_str: Human-readable expiry, already formatted by the caller.
        brand_name: Platform name for the header and footer.

    Returns:
        (subject, html_body, text_body)
    """
    subject = f"Reset your {brand_name} password"

    text_body = (
        f"Hello,\n\n"
        f"A password reset was requested for {recipient_email} on "
        f"{brand_name}.\n\n"
        f"Open the link below to choose a new password:\n"
        f"{reset_link}\n\n"
        f"This link expires on {expiry_str} and can be used once.\n\n"
        f"If you did not request this, no action is required — your password "
        f"has not changed and this link will expire unused. If you receive "
        f"these repeatedly, someone may know your email address and be "
        f"attempting to access your account."
    )

    safe_email = html.escape(recipient_email)
    safe_link = html.escape(reset_link, quote=True)
    safe_expiry = html.escape(expiry_str)

    content_html = f"""<p>Hello,</p>
            <p>A password reset was requested for <strong>{safe_email}</strong>.</p>
            <div class="button-container">
                <a href="{safe_link}" class="button">Choose a New Password</a>
            </div>
            <p>If the button above does not work, copy and paste the following URL into your browser:</p>
            <p><a href="{safe_link}">{safe_link}</a></p>
            <p>This link expires on <strong>{safe_expiry}</strong> and can be used once.</p>
            <p>If you did not request this, no action is required. Your password has not changed and this link will expire unused.</p>"""

    footer_text = (
        f"This email was sent by {brand_name}. Your password will not change "
        f"unless the link above is used."
    )

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=html.escape(subject),
        brand_name=html.escape(brand_name),
        content_html=content_html,
        footer_text=html.escape(footer_text),
    )

    return subject, html_body, text_body