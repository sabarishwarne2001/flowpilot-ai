"""
Completion notice, to BOTH parties. The A.2.2 fix.

ARCH-03 sends the password-changed notice on every change specifically because
*"it is the only signal a user gets if a reset was not theirs."* Ownership
transfer is the more consequential of the two operations and, before ARCH-05,
produced a log line and nothing else. This message is the equivalent signal.

Sent to the outgoing owner as well as the incoming one, and the reason is the
outgoing one. The incoming owner accepted; they know. The outgoing owner may
have been socially engineered into initiating, and until this message existed
the only person not told that a tenant had changed hands was the person who
used to hold it.

ONE TEMPLATE, TWO PERSPECTIVES, DELIBERATELY.
    §B.7 lists a single template sent to both parties, and that is right — it
    is one event and the facts are identical. But the two readers need
    different things from it. The new owner needs to know what they now
    control; the outgoing owner needs a security notice with a way to raise an
    alarm. Rendering identical copy to both would give the new owner a warning
    they cannot act on and the outgoing owner a welcome to something they no
    longer have.

    So `perspective` selects the framing while the facts, the subject stem and
    the layout stay shared. Two template modules would have let the two drift.

Neither perspective carries a link that grants anything, on password_changed's
reasoning: if the transfer was fraudulent, this message is being read by
someone who already controls a session, and it should hand them nothing.

Pure function: no Session, no model import, no clock. See common.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT
from app.templates.emails.common import (
    esc,
    esc_attr,
    format_timestamp,
    single_line,
)

#: Which side of the transfer the recipient is on.
#:
#: A Literal rather than a bool: send_ownership_transferred() calls this twice
#: with different values, and `is_new_owner=False` at a call site reads as a
#: negation of something rather than as "the person who used to own this".
Perspective = Literal["outgoing", "incoming"]


def render_ownership_transferred(
    *,
    recipient_email: str,
    perspective: Perspective,
    organization_name: str,
    previous_owner_email: str,
    previous_owner_display: str,
    new_owner_email: str,
    new_owner_display: str,
    transferred_at: datetime,
    brand_name: str,
    support_email: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body.

    Args:
        recipient_email: Who this copy is addressed to. Present so the body can
            name the account, exactly as password_changed does — "your
            organization changed hands" is a different message when the reader
            has three accounts.
        perspective: "outgoing" for the former owner, "incoming" for the new
            one. Selects the framing; the facts are identical either way.
        organization_name: Tenant name. Escaped, and single_line() for the
            subject.
        previous_owner_email / new_owner_email: Addresses. Every mailto: uses
            these and never the display forms (§B.6).
        previous_owner_display / new_owner_display: Names for prose, falling
            back to the address when display_name is NULL. Never in an href.
        transferred_at: Aware datetime. Rendered in UTC with the zone named:
            a bare clock time is useless to a reader two zones away trying to
            work out whether that was them.
        brand_name: Platform name for the header and footer.
        support_email: Where a reader who did not expect this should write.

    Returns:
        (subject, html_body, text_body)

    Raises:
        ValueError: perspective is not one of the two known values. Raised
            rather than defaulted — a typo silently rendering the outgoing
            owner's security notice to the new owner is worse than a failed
            send, which invitation_mail._send converts to False anyway.
    """
    if perspective not in ("outgoing", "incoming"):
        raise ValueError(
            f"Unknown perspective {perspective!r}; expected "
            f"'outgoing' or 'incoming'."
        )

    when = format_timestamp(transferred_at)

    if perspective == "incoming":
        subject = single_line(f"You are now the owner of {organization_name}")

        text_body = (
            f"Hello,\n\n"
            f"You accepted the transfer of {organization_name} on "
            f"{brand_name}, and the change is now in effect. This applies to "
            f"the account {recipient_email}.\n\n"
            f"Transferred: {when}\n"
            f"Previous owner: {previous_owner_display} "
            f"({previous_owner_email})\n\n"
            f"As owner you control the organization's members and their "
            f"roles, its seat limit, and its billing. "
            f"{previous_owner_display} is now an administrator and keeps "
            f"their access to the organization.\n\n"
            f"At least one active owner is required at all times, so you "
            f"cannot leave the organization or step down without first "
            f"transferring ownership to someone else.\n\n"
            f"If you did not expect this, contact {support_email}."
        )

        content_html = f"""<p>Hello,</p>
            <p>You accepted the transfer of
               <strong>{esc(organization_name)}</strong>, and the change is now
               in effect. This applies to the account
               <strong>{esc(recipient_email)}</strong>.</p>
            <p><strong>Transferred:</strong> {esc(when)}<br>
               <strong>Previous owner:</strong> {esc(previous_owner_display)}
               (<a href="mailto:{esc_attr(previous_owner_email)}">{esc(previous_owner_email)}</a>)</p>
            <p>As owner you control the organization&rsquo;s members and their
               roles, its seat limit, and its billing.
               {esc(previous_owner_display)} is now an administrator and keeps
               their access to the organization.</p>
            <p>At least one active owner is required at all times, so you
               cannot leave the organization or step down without first
               transferring ownership to someone else.</p>
            <p>If you did not expect this, contact
               <a href="mailto:{esc_attr(support_email)}">{esc(support_email)}</a>.</p>"""

        footer_text = (
            f"{brand_name} sent this because you accepted ownership of an "
            f"organization. It cannot be turned off."
        )

    else:
        subject = single_line(
            f"You are no longer the owner of {organization_name}"
        )

        text_body = (
            f"Hello,\n\n"
            f"Ownership of {organization_name} on {brand_name} transferred "
            f"from your account, {recipient_email}, to "
            f"{new_owner_display} ({new_owner_email}).\n\n"
            f"Transferred: {when}\n\n"
            f"You are now an administrator of {organization_name}. You keep "
            f"your access to the organization and its workspaces, but you no "
            f"longer control its seat limit, its billing, or its ownership.\n\n"
            f"If you made this change, nothing further is needed.\n\n"
            f"If you did NOT make this change, someone else may have access "
            f"to your account, and they now control an organization you used "
            f"to own. Contact {support_email} immediately, and change your "
            f"password — that signs out every device holding a session."
        )

        content_html = f"""<p>Hello,</p>
            <p>Ownership of <strong>{esc(organization_name)}</strong>
               transferred from your account,
               <strong>{esc(recipient_email)}</strong>, to
               <strong>{esc(new_owner_display)}</strong>
               (<a href="mailto:{esc_attr(new_owner_email)}">{esc(new_owner_email)}</a>).</p>
            <p><strong>Transferred:</strong> {esc(when)}</p>
            <p>You are now an administrator of
               {esc(organization_name)}. You keep your access to the
               organization and its workspaces, but you no longer control its
               seat limit, its billing, or its ownership.</p>
            <p>If you made this change, nothing further is needed.</p>
            <p><strong>If you did not make this change</strong>, someone else
               may have access to your account, and they now control an
               organization you used to own. Contact
               <a href="mailto:{esc_attr(support_email)}">{esc(support_email)}</a>
               immediately, and change your password &mdash; that signs out
               every device holding a session.</p>"""

        footer_text = (
            f"This is a security notice from {brand_name}. It was sent "
            f"because ownership of your organization changed, and cannot be "
            f"turned off."
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
