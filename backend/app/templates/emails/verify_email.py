"""
Email verification message.

The link points at a *frontend* route and carries the token in the URL
fragment, never a query string. A fragment is not transmitted to any server,
so the token cannot leak through the `Referer` header to third-party assets on
the landing page, and cannot be written to a proxy or web-server access log.
The frontend reads it, POSTs it to `/auth/verify-email`, and clears it from the
address bar. ARCH-03 §B.9.
"""

from __future__ import annotations

import html

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT


def render_verify_email(
    *,
    recipient_email: str,
    verify_link: str,
    expiry_str: str,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body for email verification.

    Args:
        recipient_email: The address being verified. Echoed back so a
            misdirected message is obvious to whoever receives it.
        verify_link: Fully-qualified frontend URL with the token in the
            fragment, e.g. https://app.flowpilot.ai/verify-email#token=...
        expiry_str: Human-readable expiry, already formatted by the caller.
        brand_name: Platform name for the header and footer.

    Returns:
        (subject, html_body, text_body)
    """
    subject = f"Verify your {brand_name} email address"

    text_body = (
        f"Hello,\n\n"
        f"Confirm that {recipient_email} belongs to you to finish setting up "
        f"your {brand_name} account.\n\n"
        f"Open the link below to verify:\n"
        f"{verify_link}\n\n"
        f"This link expires on {expiry_str} and can be used once.\n\n"
        f"If you did not create a {brand_name} account, ignore this email. "
        f"Nothing will happen until the link is used."
    )

    # Both values are interpolated into markup. The address originates from
    # user input at registration, so it is escaped; the link is escaped as an
    # attribute value.
    safe_email = html.escape(recipient_email)
    safe_link = html.escape(verify_link, quote=True)
    safe_expiry = html.escape(expiry_str)

    content_html = f"""<p>Hello,</p>
            <p>Confirm that <strong>{safe_email}</strong> belongs to you to finish setting up your account.</p>
            <div class="button-container">
                <a href="{safe_link}" class="button">Verify Email Address</a>
            </div>
            <p>If the button above does not work, copy and paste the following URL into your browser:</p>
            <p><a href="{safe_link}">{safe_link}</a></p>
            <p>This link expires on <strong>{safe_expiry}</strong> and can be used once.</p>"""

    footer_text = (
        f"This email was sent by {brand_name}. If you did not create an "
        f"account, you can safely ignore it."
    )

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=subject,
        brand_name=brand_name,
        content_html=content_html,
        footer_text=footer_text,
    )

    return subject, html_body, text_body