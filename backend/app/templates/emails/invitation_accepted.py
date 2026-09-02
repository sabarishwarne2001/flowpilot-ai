"""
Acceptance notice, to the inviter.

Reports the grants that were actually provisioned, not the grants the
invitation was issued with. Under B.2 a workspace deleted between issuance and
acceptance takes its grant with it by cascade, and acceptance provisions fewer
than it was issued with. That is correct behaviour, and the person who sent the
invitation is the one who needs to know it happened -- otherwise a colleague is
missing a workspace and nobody can account for why.

Carries no link that grants anything. The members link requires a session.
"""

from __future__ import annotations

from typing import Sequence

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT
from app.templates.emails.common import (
    GrantLine,
    esc,
    esc_attr,
    render_grant_lines_text,
    render_grant_table,
    single_line,
)


def render_invitation_accepted(
    *,
    invited_email: str,
    invited_display: str | None = None,
    organization_name: str,
    organization_role_display: str,
    provisioned_grants: Sequence[GrantLine],
    skipped_grant_count: int,
    members_url: str,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body.

    Args:
        provisioned_grants: What acceptance actually created.
        skipped_grant_count: Grants on the invitation that no longer resolved
            to a live workspace. Normally zero; when it is not, saying so is
            the entire value of this message (B.2, R8).
        members_url: Deep link to the organization member directory. Requires
            authentication; it is a destination, not a credential.
    """
    # §B.6: the address identifies, the display name is what a person reads.
    # Defaults to the address, which is what every caller passed before
    # ARCH-05 gave users a display_name, so existing behaviour is unchanged.
    display = invited_display if invited_display is not None else invited_email
    subject = single_line(f"{display} joined {organization_name}")

    if provisioned_grants:
        grants_text = (
            "Workspaces they now have access to:\n"
            f"{render_grant_lines_text(provisioned_grants)}\n\n"
        )
        grants_html = (
            "<p>Workspaces they now have access to:</p>"
            f"{render_grant_table(provisioned_grants)}"
        )
    else:
        grants_text = (
            "They were not granted access to any workspace. Their seat in the "
            "organization is active.\n\n"
        )
        grants_html = (
            "<p>They were not granted access to any workspace. Their seat in "
            "the organization is active.</p>"
        )

    if skipped_grant_count:
        noun = "workspace" if skipped_grant_count == 1 else "workspaces"
        verb = "no longer exists" if skipped_grant_count == 1 else "no longer exist"
        skipped_sentence = (
            f"{skipped_grant_count} {noun} on this invitation {verb}, so no "
            f"access was granted there."
        )
        grants_text += f"{skipped_sentence}\n\n"
        grants_html += f"<p><strong>{esc(skipped_sentence)}</strong></p>"

    text_body = (
        f"Hello,\n\n"
        f"{display} ({invited_email}) accepted your invitation to {organization_name} and "
        f"joined as {organization_role_display}.\n\n"
        f"{grants_text}"
        f"You can review the member directory here:\n"
        f"{members_url}\n"
    )

    content_html = f"""<p>Hello,</p>
            <p><strong>{esc(display)}</strong> (<a href="mailto:{esc_attr(invited_email)}">{esc(invited_email)}</a>) accepted your invitation to
               <strong>{esc(organization_name)}</strong> and joined as
               <strong>{esc(organization_role_display)}</strong>.</p>
            {grants_html}
            <div class="button-container">
                <a href="{esc_attr(members_url)}" class="button">View Members</a>
            </div>"""

    footer_text = (
        f"You are receiving this from {brand_name} because you sent this "
        f"invitation."
    )

    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        # ARCH-05 Step 8 (§0.b). esc()/html.escape(), not the raw subject.
        # BASE_EMAIL_HTML_LAYOUT interpolates this into <title>{title}</title>,
        # which is RCDATA: a tenant name or address containing "</title>"
        # closes the element early and everything after it becomes document
        # markup. single_line() does not help — it strips control characters,
        # and "<" is not one.
        title=esc(subject),
        # ARCH-05 Step 8 (§0.b, extended). BASE_EMAIL_HTML_LAYOUT interpolates
        # brand_name into <span class="logo">{brand_name}</span> and footer_text
        # into <p>{footer_text}</p> — both raw HTML contexts, neither escaped by
        # the layout. content_html is the ONLY slot that legitimately carries
        # markup; every other slot is escaped here.
        brand_name=esc(brand_name),
        content_html=content_html,
        footer_text=esc(footer_text),
    )

    return subject, html_body, text_body
