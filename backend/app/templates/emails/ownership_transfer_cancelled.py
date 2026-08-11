"""
Cancellation notice, to the target.

Travels toward the person who did NOT act, which is the same shape as
invitation_revoked.py and for the same reason: the initiator withdrew the
proposal and does not need telling; the target is holding a request they may be
about to accept and does.

The timing is what makes it worth a message rather than a status change.
Ownership is not something people decide about in the thirty seconds after the
mail arrives — §B.1's whole premise is that the target should weigh a financial
responsibility before agreeing to it. So the realistic case is that they are
still thinking about it when the proposal is withdrawn, and the realistic
failure without this message is that they open the in-app link a day later,
find nothing there, and cannot tell whether it lapsed, was cancelled, or was
never really sent.

Carries no link. The proposal it refers to no longer exists, and a link to a
page that will say "nothing here" is worse than no link.

Unlike invitation_revoked.py, this may name the organization and the initiator
without hesitation: the recipient is a verified, active member of the tenant
(A.2.3), not an unproved address that might belong to someone else entirely.

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


def render_ownership_transfer_cancelled(
    *,
    recipient_email: str,
    organization_name: str,
    initiator_email: str,
    initiator_display: str,
    cancelled_at: datetime,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body.

    Args:
        recipient_email: The target of the withdrawn proposal.
        organization_name: Tenant name. Escaped, and single_line() for the
            subject.
        initiator_email: The owner who cancelled. Used for the mailto: (§B.6).
        initiator_display: Their name for prose, falling back to their address
            when display_name is NULL. Never in an href.
        cancelled_at: Aware datetime.
        brand_name: Platform name for the header and footer.

    Returns:
        (subject, html_body, text_body)
    """
    subject = single_line(
        f"The ownership transfer for {organization_name} was withdrawn"
    )
    when = format_timestamp(cancelled_at)

    text_body = (
        f"Hello,\n\n"
        f"{initiator_display} ({initiator_email}) withdrew the request to "
        f"transfer ownership of {organization_name} on {brand_name} to you.\n\n"
        f"Withdrawn: {when}\n\n"
        f"You can no longer accept it. Nothing changed: "
        f"{initiator_display} is still the owner and you keep the role you "
        f"already had.\n\n"
        f"If you were expecting to take over, contact {initiator_email} — "
        f"they can send a new request.\n\n"
        f"No action is needed otherwise."
    )

    content_html = f"""<p>Hello,</p>
            <p><strong>{esc(initiator_display)}</strong>
               (<a href="mailto:{esc_attr(initiator_email)}">{esc(initiator_email)}</a>)
               withdrew the request to transfer ownership of
               <strong>{esc(organization_name)}</strong> to you.</p>
            <p><strong>Withdrawn:</strong> {esc(when)}</p>
            <p>You can no longer accept it. Nothing changed:
               {esc(initiator_display)} is still the owner and you keep the
               role you already had.</p>
            <p>If you were expecting to take over, contact
               <a href="mailto:{esc_attr(initiator_email)}">{esc(initiator_email)}</a>
               &mdash; they can send a new request.</p>
            <p>No action is needed otherwise.</p>"""

    footer_text = (
        f"{brand_name} sent this because an ownership transfer proposed to "
        f"you was withdrawn. No change has been made."
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