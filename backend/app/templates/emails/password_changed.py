"""
Password-changed notice.

This message carries no token and no link that grants access, because it is the
one identity email that may be read by an attacker who already controls the
session. It is deliberately a dead end: it tells the account holder what
happened and where to go, and gives a thief nothing to click.

It is also the only signal a user gets if a reset was not theirs, which is why
it is sent on *every* successful reset and every in-app password change, not
only on resets (ARCH-03 Step 9).
"""

from __future__ import annotations

import html

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT


def render_password_changed(
    *,
    recipient_email: str,
    changed_at_str: str,
    brand_name: str,
    support_email: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body for the change notice.

    Args:
        recipient_email: The account address whose password changed.
        changed_at_str: Human-readable timestamp, already formatted by the
            caller. Include the zone — "3:04" means nothing to a user two
            timezones away trying to work out whether that was them.
        brand_name: Platform name for the header and footer.
        support_email: Contact address for a user who did not make this change.

    Returns:
        (subject, html_body, text_body)
    """
    subject = f"Your {brand_name} password was changed"

    text_body = (
        f"Hello,\n\n"
        f"The password for {recipient_email} was changed on {changed_at_str}.\n\n"
        f"All active sessions were signed out as part of this change, so any "
        f"device still holding a session will need to sign in again.\n\n"
        f"If you made this change, nothing further is needed.\n\n"
        f"If you did NOT make this change, someone else may have access to "
        f"your account. Contact {support_email} immediately."
    )

    safe_email = html.escape(recipient_email)
    safe_changed_at = html.escape(changed_at_str)
    safe_support = html.escape(support_email)

    content_html = f"""<p>Hello,</p>
            <p>The password for <strong>{safe_email}</strong> was changed on <strong>{safe_changed_at}</strong>.</p>
            <p>All active sessions were signed out as part of this change, so any device still holding a session will need to sign in again.</p>
            <p>If you made this change, nothing further is needed.</p>
            <p><strong>If you did not make this change</strong>, someone else may have access to your account. Contact <a href="mailto:{safe_support}">{safe_support}</a> immediately.</p>"""

    footer_text = (
        f"This is a security notice from {brand_name}. It was sent because "
        f"your password changed, and cannot be turned off."
    )

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=html.escape(subject),
        brand_name=html.escape(brand_name),
        content_html=content_html,
        footer_text=html.escape(footer_text),
    )

    return subject, html_body, text_body