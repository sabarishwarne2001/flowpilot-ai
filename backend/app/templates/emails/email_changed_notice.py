"""
Email-changed security notice.

Sent to the FORMER address after the change has already landed (§B.1
Option A). This is the counterpart to `password_changed.py` and follows its
rule exactly: it carries no token and no link that grants access, because it
is a message that may be read by someone who has just lost control of their
account to someone else.

It names the NEW address deliberately. A user who did not make this change
needs to be able to tell support precisely what happened, and "your address
was changed to something" is not actionable where "your address was changed
to attacker@example.com" is.
"""

from __future__ import annotations

import html

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT


def render_email_changed_notice(
    *,
    old_email: str,
    new_email: str,
    changed_at_str: str,
    brand_name: str,
    support_email: str,
) -> tuple[str, str, str]:
    """
    Renders subject, HTML body, and plain-text body for the notice.

    Args:
        old_email: The address this message is sent to — the one the account
            used to hold.
        new_email: The address the account now holds. Named so a user who did
            not make this change can report it precisely.
        changed_at_str: Human-readable timestamp, already formatted by the
            caller, including the zone.
        brand_name: Platform name for the header and footer.
        support_email: Contact address for a user who did not make this
            change.

    Returns:
        (subject, html_body, text_body)
    """
    subject = f"Your {brand_name} email address was changed"

    text_body = (
        f"Hello,\n\n"
        f"The email address on the {brand_name} account previously using "
        f"{old_email} was changed to {new_email} on {changed_at_str}.\n\n"
        f"This address ({old_email}) can no longer be used to sign in to that "
        f"account. All active sessions were signed out as part of the change.\n\n"
        f"If you made this change, nothing further is needed.\n\n"
        f"If you did NOT make this change, someone else may have taken "
        f"control of your account. Contact {support_email} immediately and "
        f"include both addresses shown above."
    )

    safe_old = html.escape(old_email)
    safe_new = html.escape(new_email)
    safe_changed_at = html.escape(changed_at_str)
    safe_support = html.escape(support_email)
    safe_brand = html.escape(brand_name)

    content_html = f"""<p>Hello,</p>
            <p>The email address on the {safe_brand} account previously using <strong>{safe_old}</strong> was changed to <strong>{safe_new}</strong> on <strong>{safe_changed_at}</strong>.</p>
            <p>This address can no longer be used to sign in to that account. All active sessions were signed out as part of the change.</p>
            <p>If you made this change, nothing further is needed.</p>
            <p><strong>If you did not make this change</strong>, someone else may have taken control of your account. Contact <a href="mailto:{safe_support}">{safe_support}</a> immediately and include both addresses shown above.</p>"""

    footer_text = (
        f"This is a security notice from {brand_name}. It was sent because "
        f"your account's email address changed, and cannot be turned off."
    )

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=html.escape(subject),
        brand_name=safe_brand,
        content_html=content_html,
        footer_text=html.escape(footer_text),
    )

    return subject, html_body, text_body
