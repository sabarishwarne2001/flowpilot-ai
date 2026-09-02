"""
Ownership transfer proposal, to the target.

The only one of the four ARCH-05 messages that asks for an action, and the only
one that carries a link. That link is NOT a credential (§B.1): the target is
already an authenticated member of this organization, so acceptance happens
in-app while signed in and there is nothing in the URL worth stealing. No
token, no hashing, no fragment-versus-query decision, no expiry window that a
leaked link widens. The link is a signpost to a page that will re-authorize the
reader on arrival.

That distinction is why this template may be direct where
organization_invitation.py has to be careful. An invitation travels to an
address nobody has proved; this travels to a verified member of the tenant
(A.2.3), so it may name the organization and the outgoing owner plainly.

What it must not do is understate what is being handed over. Ownership carries
seat_limit authority today and billing liability under Phase F, and the whole
point of §B.1's two-phase design is that nobody becomes liable for a tenant
without agreeing. A message that reads like a role tweak would defeat the
mechanism it exists to serve, so the responsibilities are stated before the
button rather than after it.

Pure function: no Session, no model import, no clock. See common.py.
"""

from __future__ import annotations

from datetime import datetime

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT
from app.templates.emails.common import (
    esc,
    esc_attr,
    format_timestamp,
    single_line,
)


def render_ownership_transfer_requested(
    *,
    recipient_email: str,
    organization_name: str,
    initiator_email: str,
    initiator_display: str,
    review_link: str,
    expires_at: datetime,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body.

    Args:
        recipient_email: The proposed new owner's address.
        organization_name: Tenant name. Free text; escaped, and passed through
            single_line() before it reaches the subject header.
        initiator_email: The outgoing owner's address. Used for every mailto:
            and for the Reply-To the mail service sets. §B.6: this must be an
            address, never a display name — `mailto:Jane Smith` is a dead link.
        initiator_display: The outgoing owner's name for prose, falling back to
            their address when display_name is NULL. Never used in an href.
        review_link: In-app URL where the proposal is accepted or declined.
            Carries no token.
        expires_at: Aware datetime. §B.8 gives proposals a 7-day TTL enforced
            lazily in the acceptance WHERE clause, so the reader needs to know
            the deadline is real.
        brand_name: Platform name for the header and footer.

    Returns:
        (subject, html_body, text_body)
    """
    subject = single_line(
        f"{initiator_display} wants to transfer ownership of "
        f"{organization_name} to you"
    )
    expiry_str = format_timestamp(expires_at)

    text_body = (
        f"Hello,\n\n"
        f"{initiator_display} ({initiator_email}) has asked to transfer "
        f"ownership of {organization_name} on {brand_name} to you.\n\n"
        f"Nothing has changed yet. The transfer happens only if you accept.\n\n"
        f"If you accept, you become the organization's owner. That means you "
        f"control its members and their roles, its seat limit, and its "
        f"billing. {initiator_display} becomes an administrator and keeps "
        f"their access to the organization, but stops being responsible "
        f"for it.\n\n"
        f"Review the request:\n"
        f"{review_link}\n\n"
        f"This request expires on {expiry_str}. After that it can no longer "
        f"be accepted and {initiator_display} would need to send a new one.\n\n"
        f"If you were not expecting this, decline it and contact "
        f"{initiator_email} before doing anything else. A transfer request is "
        f"the kind of thing a compromised account is used for."
    )

    content_html = f"""<p>Hello,</p>
            <p><strong>{esc(initiator_display)}</strong>
               (<a href="mailto:{esc_attr(initiator_email)}">{esc(initiator_email)}</a>)
               has asked to transfer ownership of
               <strong>{esc(organization_name)}</strong> to you.</p>
            <p><strong>Nothing has changed yet.</strong> The transfer happens
               only if you accept.</p>
            <p>If you accept, you become the organization&rsquo;s owner. That
               means you control its members and their roles, its seat limit,
               and its billing. {esc(initiator_display)} becomes an
               administrator and keeps their access to the organization, but
               stops being responsible for it.</p>
            <div class="button-container">
              <a class="button" href="{esc_attr(review_link)}">Review this request</a>
            </div>
            <p>This request expires on <strong>{esc(expiry_str)}</strong>.
               After that it can no longer be accepted, and
               {esc(initiator_display)} would need to send a new one.</p>
            <p>If you were not expecting this, decline it and contact
               <a href="mailto:{esc_attr(initiator_email)}">{esc(initiator_email)}</a>
               before doing anything else. A transfer request is the kind of
               thing a compromised account is used for.</p>"""

    footer_text = (
        f"{brand_name} sent this because someone proposed transferring an "
        f"organization you belong to. No change has been made."
    )

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        # esc(), not the raw subject. BASE_EMAIL_HTML_LAYOUT interpolates this
        # into <title>{title}</title>, which is RCDATA — a tenant name
        # containing "</title><script>" closes the element and lands in the
        # document. single_line() does not help: it strips control characters,
        # and "<" is not one. Every ARCH-03 and ARCH-04 template passes the raw
        # subject here and shares the gap; see the Step 2 verification notes.
        title=esc(subject),
        brand_name=brand_name,
        content_html=content_html,
        footer_text=footer_text,
    )

    return subject, html_body, text_body
