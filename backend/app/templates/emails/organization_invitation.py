"""
The invitation itself: FlowPilot writing to someone who is not yet a member of
anything, on behalf of a tenant they cannot yet see.

Replaces workspace_invitation.py, which could describe exactly one workspace at
exactly one role because that was all an invitation could carry. This one takes
a list, and the list may be empty.

The zero-grant branch is not an edge case to defend against -- it is how a
BILLING manager is onboarded (B.1), and getting its copy right is most of the
reason the template was rewritten rather than extended. "Workspaces: (none)"
would read as a bug to the one recipient for whom it is the correct outcome.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from app.templates.emails.common import (
    GrantLine,
    esc,
    esc_attr,
    format_timestamp,
    render_grant_lines_text,
    single_line,
)


def render_organization_invitation(
    *,
    invited_email: str,
    organization_name: str,
    inviter_email: str,
    inviter_display: str | None = None,
    organization_role_display: str,
    grants: Sequence[GrantLine],
    accept_link: str,
    expires_at: datetime,
    brand_name: str,
) -> tuple[str, str, str]:
    """
    Renders the subject, HTML body, and plain-text body of an invitation.

    Args:
        invited_email: The address the invitation was issued to. Stated in the
            body because acceptance requires the actor's session email to
            match it (ARCH-03 B.4 Option 2, preserved by ARCH-04 B.5). A
            recipient who signs in with a different address gets a rejection
            they cannot interpret unless the email warned them first.
        organization_name: Tenant-supplied. Escaped, and sanitised before it
            reaches the subject.
        inviter_email: The inviter's address. Always what any mailto: href
            points at, and what invitation_mail sets as Reply-To. §B.6.
        inviter_display: The inviter's name for prose, defaulting to
            inviter_email when omitted. ARCH-05 Step 8 split this from the
            single parameter that used to serve both roles. Before ARCH-05
            `users` had no name column, so the one value passed here was
            always an address and the conflation was safe by accident; now
            that display_name exists, a real name reaching an href would
            produce `mailto:Jane Smith`. Never used in an href.
        organization_role_display: ADMIN, BILLING or MEMBER. OWNER is not
            invitable (B.4) and this template will never see it.
        grants: Workspace grants attached to the invitation. May be empty.
        accept_link: Fully built by build_invitation_accept_link. This template
            must never construct a link itself, or the B.10 shape would have
            two definitions.
        expires_at: Aware datetime. format_timestamp raises on a naive one.

    Returns:
        (subject, html_body, text_body)
    """
    display = inviter_display if inviter_display is not None else inviter_email
    subject = single_line(f"You have been invited to join {organization_name}")
    expiry_str = format_timestamp(expires_at)
    expires_in = _time_left(expires_at)
    role_label = _label(organization_role_display)

    # ---- plain text -----------------------------------------------------
    if grants:
        grants_text = (
            "This invitation also gives you access to these workspaces:\n"
            f"{render_grant_lines_text(grants)}\n\n"
        )
    else:
        grants_text = (
            "This invitation does not include access to any workspaces. An "
            "administrator can add you to workspaces at any time after you "
            "join.\n\n"
        )

    text_body = (
        f"Hello,\n\n"
        f"{display} has invited you to join {organization_name} on "
        f"{brand_name}.\n\n"
        f"Organization: {organization_name}\n"
        f"Your role: {role_label}\n\n"
        f"{grants_text}"
        f"Accept the invitation:\n"
        f"{accept_link}\n\n"
        f"This invitation was sent to {invited_email}. New to {brand_name}? "
        f"The link lets you create your account in one step. Already have an "
        f"account? Sign in with that address to accept it -- signing in with a "
        f"different address will not work.\n\n"
        f"This link expires on {expiry_str} ({expires_in}).\n\n"
        f"The link is personal to {invited_email}: do not forward this "
        f"email. If you were not expecting it, ignore it. Nothing happens "
        f"until the link is used.\n\n"
        f"Questions about this invitation? Reply to this email to reach "
        f"{display}."
    )

    # ---- html -----------------------------------------------------------
    safe_link = esc_attr(accept_link)

    if grants:
        rows = "".join(
            f"""<tr>
                <td class="fp-strong fp-rule" style="padding:8px 0;border-top:1px solid #e5e7eb;font-size:14px;line-height:20px;color:#111827;">{esc(grant.workspace_name)}</td>
                <td class="fp-rule" align="right" style="padding:8px 0;border-top:1px solid #e5e7eb;">{_badge(grant.role_display)}</td>
              </tr>"""
            for grant in grants
        )
        workspaces_html = f"""<p style="margin:0 0 4px 0;font-size:12px;line-height:18px;color:#6b7280;text-transform:uppercase;letter-spacing:0.04em;">Workspaces</p>
              <p class="fp-text" style="margin:0 0 4px 0;font-size:13px;line-height:20px;color:#4b5563;">This invitation also gives you access to these workspaces:</p>
              <table role="presentation" class="grants" width="100%" border="0" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
                {rows}
              </table>"""
    else:
        workspaces_html = """<p class="fp-text" style="margin:0;font-size:13px;line-height:20px;color:#4b5563;">This invitation does not include access to any workspaces. An
                administrator can add you to workspaces at any time after you join.</p>"""

    html_body = f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="x-apple-disable-message-reformatting">
  <meta name="color-scheme" content="light dark">
  <meta name="supported-color-schemes" content="light dark">
  <title>{esc(subject)}</title>
  <!--[if mso]>
  <noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript>
  <![endif]-->
  <style>
    body, table, td, a {{ -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }}
    table, td {{ mso-table-lspace:0pt; mso-table-rspace:0pt; }}
    a[x-apple-data-detectors] {{ color:inherit !important; text-decoration:none !important; }}
    @media screen and (max-width: 620px) {{
      .fp-shell {{ width:100% !important; }}
      .fp-pad {{ padding-left:20px !important; padding-right:20px !important; }}
      .fp-button a {{ width:100% !important; }}
    }}
    @media (prefers-color-scheme: dark) {{
      .fp-page {{ background-color:#0b1120 !important; }}
      .fp-panel {{ background-color:#111827 !important; border-color:#1f2937 !important; }}
      .fp-card {{ background-color:#0f172a !important; border-color:#1f2937 !important; }}
      .fp-strong {{ color:#f9fafb !important; }}
      .fp-text {{ color:#d1d5db !important; }}
      .fp-rule {{ border-color:#1f2937 !important; }}
    }}
  </style>
</head>
<body class="fp-page" style="margin:0;padding:0;background-color:#f3f4f6;">
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#f3f4f6;">{esc(display)} invited you to join {esc(organization_name)} on {esc(brand_name)}. The invitation expires {esc(expires_in)}.</div>
  <table role="presentation" class="fp-page" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color:#f3f4f6;">
    <tr>
      <td align="center" style="padding:32px 12px;">
        <!--[if mso]><table role="presentation" width="600" border="0" cellspacing="0" cellpadding="0" align="center"><tr><td><![endif]-->
        <table role="presentation" class="fp-shell" width="600" border="0" cellspacing="0" cellpadding="0" style="width:600px;max-width:600px;">
          <tr>
            <td class="fp-pad" style="padding:0 32px 20px 32px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
              <table role="presentation" border="0" cellspacing="0" cellpadding="0">
                <tr>
                  <td width="32" height="32" align="center" bgcolor="#2563eb" style="width:32px;height:32px;border-radius:8px;background-color:#2563eb;color:#ffffff;font-size:16px;font-weight:700;line-height:32px;">F</td>
                  <td class="fp-strong" style="padding-left:10px;font-size:18px;font-weight:700;line-height:32px;color:#111827;">{esc(brand_name)}</td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td class="fp-panel fp-pad" style="padding:36px 32px;background-color:#ffffff;border:1px solid #e5e7eb;border-radius:14px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
              <h1 class="fp-strong" style="margin:0 0 12px 0;font-size:24px;line-height:32px;font-weight:700;color:#111827;">You're invited to join {esc(organization_name)}</h1>
              <p class="fp-text" style="margin:0 0 24px 0;font-size:15px;line-height:24px;color:#374151;">Hello, <strong>{esc(display)}</strong> has invited you to join <strong>{esc(organization_name)}</strong> on {esc(brand_name)}.</p>

              <table role="presentation" class="fp-card" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color:#f9fafb;border:1px solid #e5e7eb;border-radius:12px;">
                <tr>
                  <td style="padding:20px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
                    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                      <tr>
                        <td style="padding:0 0 4px 0;font-size:12px;line-height:18px;color:#6b7280;text-transform:uppercase;letter-spacing:0.04em;">Organization</td>
                        <td align="right" style="padding:0 0 4px 0;font-size:12px;line-height:18px;color:#6b7280;text-transform:uppercase;letter-spacing:0.04em;">Your role</td>
                      </tr>
                      <tr>
                        <td class="fp-strong" style="padding:0 0 16px 0;font-size:16px;line-height:24px;font-weight:700;color:#111827;">{esc(organization_name)}</td>
                        <td align="right" style="padding:0 0 16px 0;">{_badge(organization_role_display)}</td>
                      </tr>
                    </table>
                    {workspaces_html}
                  </td>
                </tr>
              </table>

              <table role="presentation" class="fp-button" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin:28px 0 8px 0;">
                <tr>
                  <td align="center">
                    <!--[if mso]>
                    <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="{safe_link}" style="height:48px;v-text-anchor:middle;width:260px;" arcsize="17%" stroke="f" fillcolor="#2563eb">
                      <w:anchorlock/>
                      <center style="color:#ffffff;font-family:Arial,sans-serif;font-size:16px;font-weight:bold;">Accept Invitation</center>
                    </v:roundrect>
                    <![endif]-->
                    <!--[if !mso]><!-->
                    <a href="{safe_link}" style="display:inline-block;width:260px;background-color:#2563eb;border-radius:8px;color:#ffffff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:16px;font-weight:600;line-height:48px;text-align:center;text-decoration:none;-webkit-text-size-adjust:none;">Accept Invitation</a>
                    <!--<![endif]-->
                  </td>
                </tr>
              </table>

              <p class="fp-text" style="margin:16px 0 4px 0;font-size:13px;line-height:20px;color:#6b7280;">Button not working? Copy this link into your browser:</p>
              <p style="margin:0 0 24px 0;font-size:13px;line-height:20px;word-break:break-all;"><a href="{safe_link}" style="color:#2563eb;text-decoration:underline;">{esc(accept_link)}</a></p>

              <p class="fp-text" style="margin:0;font-size:14px;line-height:22px;color:#374151;">This invitation was sent to <strong>{esc(invited_email)}</strong>. New to {esc(brand_name)}? The link lets you create your account in one step. Already have an account? Sign in with that address to accept it &mdash; signing in with a different address will not work.</p>
            </td>
          </tr>
          <tr>
            <td class="fp-pad" style="padding:20px 32px 0 32px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
              <table role="presentation" class="fp-rule" width="100%" border="0" cellspacing="0" cellpadding="0" style="border-top:1px solid #e5e7eb;">
                <tr>
                  <td class="fp-text" style="padding-top:16px;font-size:12px;line-height:19px;color:#6b7280;">
                    <p style="margin:0 0 8px 0;"><strong>Security:</strong> this link expires on <strong>{esc(expiry_str)}</strong> ({esc(expires_in)}). It is personal to {esc(invited_email)}, so please do not forward this email. If you were not expecting it, ignore it: nothing happens until the link is used.</p>
                    <p style="margin:0 0 8px 0;"><strong>Questions?</strong> Reply to this email to reach {esc(display)}, who sent the invitation.</p>
                    <p style="margin:0;">Sent by {esc(brand_name)} on behalf of {esc(organization_name)}.</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
        <!--[if mso]></td></tr></table><![endif]-->
      </td>
    </tr>
  </table>
</body>
</html>
"""

    return subject, html_body, text_body


def _label(value: str) -> str:
    """ADMIN -> Admin, for a badge."""
    text = (value or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:].lower()


def _badge(value: str) -> str:
    """A role as a small pill. Outlook drops the rounding and keeps the colour."""
    return (
        '<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
        "background-color:#eef2ff;color:#3730a3;font-size:12px;line-height:20px;"
        f'font-weight:600;white-space:nowrap;">{esc(_label(value))}</span>'
    )


def _time_left(expires_at: datetime) -> str:
    """How long the link stays valid, from now, in plain words ("in 3 days")."""
    hours = max(0, round((expires_at - datetime.now(expires_at.tzinfo)).total_seconds() / 3600))
    if hours >= 48:
        days = round(hours / 24)
        return f"in {days} days"
    if hours >= 1:
        return f"in {hours} hour{'s' if hours != 1 else ''}"
    return "within the hour"
