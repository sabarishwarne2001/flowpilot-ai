"""
Email-change confirmation link.

Sent to the PROPOSED new address and only there (§B.1 Option A). The old
address receives nothing at this stage — it is told after the change lands,
by `email_changed_notice`.

Unlike `password_changed.py`, this message DOES carry a credential: the link
in it authorises a change of account identity. That is why the link is a
fragment URL (see `email_change_service.build_email_change_link`) and why
the copy tells a recipient who did not expect this message to ignore it
rather than to click anything.
"""

from __future__ import annotations

import html

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT


def render_email_change_verify(
    *,
    recipient_email: str,
    confirm_link: str,
    expiry_str: str,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders subject, HTML body, and plain-text body for the confirmation.

    Args:
        recipient_email: The proposed new address. Shown so the recipient can
            confirm the request names the address they expect.
        confirm_link: Fragment-form URL carrying the token.
        expiry_str: Human-readable expiry, already formatted by the caller.
            Includes the zone — "3:04" means nothing to a user two timezones
            away trying to decide whether a link is still live.
        brand_name: Platform name for the header and footer.

    Returns:
        (subject, html_body, text_body)
    """
    subject = f"Confirm your new {brand_name} email address"

    text_body = (
        f"Hello,\n\n"
        f"Someone asked to change a {brand_name} account's email address to "
        f"{recipient_email}.\n\n"
        f"To confirm, open this link before {expiry_str}:\n\n"
        f"{confirm_link}\n\n"
        f"Confirming will sign the account out on every device, including "
        f"this one.\n\n"
        f"If you were not expecting this, ignore this message. Nothing "
        f"changes unless the link above is opened, and this address will not "
        f"be added to any account."
    )

    safe_email = html.escape(recipient_email)
    safe_link = html.escape(confirm_link, quote=True)
    safe_expiry = html.escape(expiry_str)
    safe_brand = html.escape(brand_name)

    content_html = f"""<p>Hello,</p>
            <p>Someone asked to change a {safe_brand} account's email address to <strong>{safe_email}</strong>.</p>
            <p><a href="{safe_link}">Confirm this address</a></p>
            <p>This link stops working after <strong>{safe_expiry}</strong>.</p>
            <p>Confirming will sign the account out on every device, including this one.</p>
            <p><strong>If you were not expecting this</strong>, ignore this message. Nothing changes unless the link above is opened, and this address will not be added to any account.</p>"""

    footer_text = (
        f"This message was sent by {brand_name} because someone asked to use "
        f"this address on an account."
    )

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=html.escape(subject),
        brand_name=safe_brand,
        content_html=content_html,
        footer_text=html.escape(footer_text),
    )

    return subject, html_body, text_body
