"""
Decline notice, to the initiator.

§B.7's justification is one sentence and it is the whole reason this template
exists: *otherwise a declined proposal looks identical to an ignored one.*

Under §B.8 there is no sweeper. A proposal that is declined and a proposal
that is sitting unread both show as "not accepted" in the initiator's in-app
view until the 7-day TTL lapses. Without this message the initiator's only
options are to wait a week or to ask in person, and the most likely outcome is
that they cancel and re-propose to the same person — who declines again.

Deliberately does not report a reason, because none is collected. §B.1's
decline is a single action, not a form. Inventing a reason field so this
message could quote it would put a person's stated refusal into an email, and
"I don't want the billing liability" is a thing people say to a colleague and
not a thing they want minuted.

No link. The initiator is a signed-in member who can propose again from the
same page they proposed from; a link here would add a surface to no purpose.

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


def render_ownership_transfer_declined(
    *,
    recipient_email: str,
    organization_name: str,
    target_email: str,
    target_display: str,
    declined_at: datetime,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body.

    Args:
        recipient_email: The initiator, who is still the owner.
        organization_name: Tenant name. Escaped, and single_line() for the
            subject.
        target_email: The address that declined. Used for the mailto: (§B.6).
        target_display: That person's name for prose, falling back to their
            address when display_name is NULL. Never in an href.
        declined_at: Aware datetime.
        brand_name: Platform name for the header and footer.

    Returns:
        (subject, html_body, text_body)
    """
    subject = single_line(
        f"{target_display} declined ownership of {organization_name}"
    )
    when = format_timestamp(declined_at)

    text_body = (
        f"Hello,\n\n"
        f"{target_display} ({target_email}) declined your request to transfer "
        f"ownership of {organization_name} on {brand_name}.\n\n"
        f"Declined: {when}\n\n"
        f"Nothing changed. You are still the owner of {organization_name}, "
        f"and {target_display} keeps the role they already had.\n\n"
        f"You can propose the transfer to someone else whenever you are "
        f"ready. If you think this was a misunderstanding, ask "
        f"{target_email} before sending another request — a second identical "
        f"proposal is rarely the answer to a decline."
    )

    content_html = f"""<p>Hello,</p>
            <p><strong>{esc(target_display)}</strong>
               (<a href="mailto:{esc_attr(target_email)}">{esc(target_email)}</a>)
               declined your request to transfer ownership of
               <strong>{esc(organization_name)}</strong>.</p>
            <p><strong>Declined:</strong> {esc(when)}</p>
            <p>Nothing changed. You are still the owner of
               {esc(organization_name)}, and {esc(target_display)} keeps the
               role they already had.</p>
            <p>You can propose the transfer to someone else whenever you are
               ready. If you think this was a misunderstanding, ask
               <a href="mailto:{esc_attr(target_email)}">{esc(target_email)}</a>
               before sending another request &mdash; a second identical
               proposal is rarely the answer to a decline.</p>"""

    footer_text = (
        f"{brand_name} sent this because an ownership transfer you proposed "
        f"was declined."
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