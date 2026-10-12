"""An organization notice, by email (campaign session 3, N-062).

The in-app notices about billing, security, plan limits and automations are also emailed
(`outbox_dispatcher._send_email`). This is their one layout: the notice itself, which
organization it is about, a link to act when there is one, and why the person got it, with a
link to their email choices. Security and Billing say they cannot be turned off; the others say
how to turn them off.

The title and body are the in-app notice's own words (already through the output filter);
they can carry names people typed (an organization, a domain, an add-on), so everything is
escaped here and the subject is kept to one line.

Pure function: no Session, no model import, no clock. See common.py.
"""

from __future__ import annotations

from typing import Optional

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT
from app.templates.emails.common import esc, esc_attr, single_line


def render_account_notice(
    *,
    title: str,
    body: str,
    organization_name: Optional[str],
    category_label: str,
    essential: bool,
    action_url: Optional[str],
    action_label: Optional[str],
    preferences_url: str,
    brand_name: str,
) -> tuple[str, str, str]:
    """Render `(subject, html, text)` for one notice to one person."""
    subject = single_line(title)
    organization = single_line(organization_name) if organization_name else None
    about = f"This is about {organization}." if organization else None

    if essential:
        why = (
            f"You get {category_label.lower()} emails because they concern your account or an "
            f"organization you belong to on {brand_name}. They cannot be turned off."
        )
        manage = "Choose which other emails you get"
    else:
        why = f"You get {category_label.lower()} emails because they are turned on in your settings."
        manage = "Turn these emails off"

    text_parts = ["Hello,", body]
    if about:
        text_parts.append(about)
    if action_url:
        text_parts.append(f"{action_label or 'Open'}: {action_url}")
    text_parts.append(f"{why}\n{manage}: {preferences_url}")
    text_body = "\n\n".join(text_parts) + "\n"

    action_html = (
        f"""
            <div class="button-container">
                <a class="button" href="{esc_attr(action_url)}">{esc(action_label or 'Open')}</a>
            </div>"""
        if action_url
        else ""
    )
    about_html = f"\n            <p>{esc(about)}</p>" if about else ""
    content_html = f"""<p>Hello,</p>
            <p>{esc(body)}</p>{about_html}{action_html}
            <p style="font-size:12px;color:#6b7280">{esc(why)}
               <a href="{esc_attr(preferences_url)}">{esc(manage)}</a>.</p>"""

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=esc(subject),
        brand_name=esc(brand_name),
        content_html=content_html,
        footer_text=esc(f"{brand_name} sent this {category_label.lower()} notice."),
    )
    return subject, html_body, text_body


__all__ = ["render_account_notice"]
