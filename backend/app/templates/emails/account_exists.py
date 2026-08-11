"""
"Someone tried to register with your address" notice.

Sent when a registration attempt names an address that already has an account.

The point is not to be helpful — it is to make the registration endpoint's
response independent of whether the account exists. Something has to happen in
both branches, and the only thing that can differ is what lands in a mailbox
the requester may not be able to read.

For the real owner it is a useful signal: either they forgot they had an
account, or somebody is probing for theirs. For a prober it is nothing at all,
because they never see it.

No link that grants access. Like the password-changed notice, this message may
be read by someone who is not the account holder, so it points at the sign-in
and reset pages rather than carrying anything clickable that authenticates.
"""

from __future__ import annotations

import html

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT


def render_account_exists(
    *,
    recipient_email: str,
    login_url: str,
    reset_url: str,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body.

    Args:
        recipient_email: The address someone tried to register.
        login_url: Where to sign in. Carries no token.
        reset_url: Where to start a password reset. Carries no token.
        brand_name: Platform name for the header and footer.

    Returns:
        (subject, html_body, text_body)
    """
    subject = f"You already have a {brand_name} account"

    text_body = (
        f"Hello,\n\n"
        f"Someone just tried to create a {brand_name} account using "
        f"{recipient_email}. An account with this address already exists, so "
        f"no new one was created and nothing has changed.\n\n"
        f"If that was you, sign in instead:\n"
        f"{login_url}\n\n"
        f"If you have forgotten your password, you can reset it:\n"
        f"{reset_url}\n\n"
        f"If it was not you, no action is needed. Someone entered your address "
        f"on a sign-up form; they have not gained any access to your account."
    )

    safe_email = html.escape(recipient_email)
    safe_login = html.escape(login_url, quote=True)
    safe_reset = html.escape(reset_url, quote=True)

    content_html = f"""<p>Hello,</p>
            <p>Someone just tried to create an account using <strong>{safe_email}</strong>. An account with this address already exists, so no new one was created and nothing has changed.</p>
            <p>If that was you, sign in instead:</p>
            <div class="button-container">
                <a href="{safe_login}" class="button">Sign In</a>
            </div>
            <p>If you have forgotten your password, you can <a href="{safe_reset}">reset it</a>.</p>
            <p>If it was not you, no action is needed. Someone entered your address on a sign-up form; they have not gained any access to your account.</p>"""

    footer_text = (
        f"This email was sent by {brand_name} because someone attempted to "
        f"register with your address."
    )

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=html.escape(subject),
        brand_name=html.escape(brand_name),
        content_html=content_html,
        footer_text=html.escape(footer_text),
    )

    return subject, html_body, text_body