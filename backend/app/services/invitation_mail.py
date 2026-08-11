"""
Delivery surface for the ARCH-04 invitation lifecycle.

Seven events, seven messages: the invitation itself, the acceptance notice to
the inviter, the rejection notice to the inviter, the revocation notice to the
invitee, the seat-blocked notice to the inviter, the expiry digest to the
inviter, and the resend notice to the invitee.

Mirrors password_mail.py deliberately and almost line for line. That module
already argued for this shape at ARCH-03 Step 9 and every reason it gave still
applies here; a second module with a different shape would mean two mail
conventions to keep in your head, and the boundary tests would have to be
written twice.

Restated because the reasons are load-bearing rather than stylistic:

  - NOT part of NotificationService. send_notification writes a row to
    `notifications`, where workspace_id is NOT NULL and every read is
    workspace-scoped by the ARCH-02 isolation suite. An invitation travels
    to someone who holds no workspace grant yet, so supplying a workspace_id
    would file the notification into a timeline the recipient cannot read, or
    into a foreign workspace they have not joined.
  - NO Session, no app.crud import, no ORM import. Every function takes
    primitives. Step 1 is what creates the templates and mail service; Step 6 is
    what wires them to the service boundary.
  - Taking no Session also means these cannot inherit the ARCH-03 background
    task hazard, where a request-scoped Session handed to a BackgroundTask
    stays valid only by dependency-teardown ordering.
  - Every function returns bool and raises nothing. These are courtesy notices
    dispatched from post-commit carriers (§D7.1). An invitation issuance must
    not roll back because an email bounced — the invitation is valid; the email
    is how the recipient finds out it exists.

WHAT THIS MODULE MUST NEVER DO
    Log a subject, a body, or a link carrying a token. ARCH-03 R4. The accept
    link carries a secret that grants tenancy on use; logging it writes a
    tenancy-granting credential to stdout and every log collector between the
    process and disk.

THE ONE EXCEPTION TO "RAISES NOTHING"
    An exception raised inside a BackgroundTask is caught by Starlette and
    logged; it does not fail the response, which was already sent. But
    send_platform_email raises PlatformEmailNotConfigured when PLATFORM_SMTP_HOST
    is empty outside development. Catching that here and logging a warning means
    a missing SMTP config in production produces a visible warning log line on
    every invitation attempt rather than an unhandled traceback.
"""

from __future__ import annotations

import logging
import uuid
from typing import Callable, Sequence

from app.core.config import settings as app_settings
from app.core.platform_email import (
    PlatformEmailNotConfigured,
    send_platform_email,
)
from app.templates.emails.common import ExpiredInvitationLine, GrantLine
from app.templates.emails.invitation_accepted import (
    render_invitation_accepted,
)
from app.templates.emails.invitation_expiry_digest import (
    render_invitation_expiry_digest,
)
from app.templates.emails.invitation_rejected import (
    render_invitation_rejected,
)
from app.templates.emails.invitation_revoked import (
    render_invitation_revoked,
)
from app.templates.emails.invitation_seat_blocked import (
    render_invitation_seat_blocked,
)
from app.templates.emails.organization_invitation import (
    render_organization_invitation,
)

logger = logging.getLogger("app.services.invitation_mail")

#: Rendered message: (subject, html_body, text_body).
RenderedMessage = tuple[str, str, str]


def _send(
    *,
    event: str,
    recipient: str,
    render: Callable[[], RenderedMessage],
    invitation_id: uuid.UUID | None = None,
    reply_to: str | None = None,
) -> bool:
    try:
        subject, html_body, text_body = render()
    except Exception:
        logger.exception(
            "INVITATION_MAIL_RENDER_FAILED | event=%s | invitation=%s",
            event,
            invitation_id,
        )
        return False

    try:
        delivered, detail = send_platform_email(
            recipient=recipient,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            reply_to=reply_to,
        )
    except PlatformEmailNotConfigured as exc:
        logger.error(
            "INVITATION_MAIL_UNCONFIGURED | event=%s | invitation=%s | %s",
            event,
            invitation_id,
            exc,
        )
        return False
    except Exception:
        logger.exception(
            "INVITATION_MAIL_ERROR | event=%s | invitation=%s",
            event,
            invitation_id,
        )
        return False

    if delivered:
        logger.info(
            "INVITATION_MAIL_SENT | event=%s | invitation=%s | to=%s",
            event,
            invitation_id,
            recipient,
        )
    else:
        logger.warning(
            "INVITATION_MAIL_FAILED | event=%s | invitation=%s | to=%s | %s",
            event,
            invitation_id,
            recipient,
            detail,
        )

    return delivered


def send_invitation(
    *,
    invited_email: str,
    organization_name: str,
    inviter_email: str,
    inviter_display: str | None = None,
    organization_role_display: str,
    grants: Sequence[GrantLine],
    accept_link: str,
    expires_at: any,
    invitation_id: uuid.UUID | None = None,
) -> bool:
    return _send(
        event="INVITATION_ISSUED",
        recipient=invited_email,
        invitation_id=invitation_id,
        reply_to=inviter_email,
        render=lambda: render_organization_invitation(
            invited_email=invited_email,
            organization_name=organization_name,
            inviter_email=inviter_email,
            inviter_display=inviter_display,
            organization_role_display=organization_role_display,
            grants=grants,
            accept_link=accept_link,
            expires_at=expires_at,
            brand_name=app_settings.PROJECT_NAME,
        ),
    )


def send_invitation_revoked(
    *,
    invited_email: str,
    organization_name: str,
    inviter_email: str,
    inviter_display: str | None = None,
    invitation_id: uuid.UUID | None = None,
) -> bool:
    return _send(
        event="INVITATION_REVOKED",
        recipient=invited_email,
        invitation_id=invitation_id,
        render=lambda: render_invitation_revoked(
            invited_email=invited_email,
            organization_name=organization_name,
            inviter_email=inviter_email,
            inviter_display=inviter_display,
            brand_name=app_settings.PROJECT_NAME,
        ),
    )


def send_invitation_accepted(
    *,
    inviter_email: str,
    invited_email: str,
    invited_display: str | None = None,
    organization_name: str,
    organization_role_display: str,
    provisioned_grants: Sequence[GrantLine],
    skipped_grant_count: int,
    members_url: str,
    invitation_id: uuid.UUID | None = None,
) -> bool:
    return _send(
        event="INVITATION_ACCEPTED",
        recipient=inviter_email,
        invitation_id=invitation_id,
        render=lambda: render_invitation_accepted(
            invited_email=invited_email,
            invited_display=invited_display,
            organization_name=organization_name,
            organization_role_display=organization_role_display,
            provisioned_grants=provisioned_grants,
            skipped_grant_count=skipped_grant_count,
            members_url=members_url,
            brand_name=app_settings.PROJECT_NAME,
        ),
    )


def send_invitation_rejected(
    *,
    inviter_email: str,
    invited_email: str,
    organization_name: str,
    invitations_url: str,
    invitation_id: uuid.UUID | None = None,
) -> bool:
    return _send(
        event="INVITATION_REJECTED",
        recipient=inviter_email,
        invitation_id=invitation_id,
        render=lambda: render_invitation_rejected(
            invited_email=invited_email,
            organization_name=organization_name,
            invitations_url=invitations_url,
            brand_name=app_settings.PROJECT_NAME,
        ),
    )


def send_invitation_seat_blocked(
    *,
    inviter_email: str,
    invited_email: str,
    organization_name: str,
    seat_limit: int | None,
    members_url: str,
    invitation_id: uuid.UUID | None = None,
) -> bool:
    return _send(
        event="INVITATION_SEAT_BLOCKED",
        recipient=inviter_email,
        invitation_id=invitation_id,
        render=lambda: render_invitation_seat_blocked(
            invited_email=invited_email,
            organization_name=organization_name,
            seat_limit=seat_limit,
            members_url=members_url,
            brand_name=app_settings.PROJECT_NAME,
        ),
    )


def send_invitation_expiry_digest(
    *,
    inviter_email: str,
    lines: Sequence[ExpiredInvitationLine],
    invitations_url: str,
) -> bool:
    return _send(
        event="INVITATION_EXPIRY_DIGEST",
        recipient=inviter_email,
        render=lambda: render_invitation_expiry_digest(
            lines=lines,
            invitations_url=invitations_url,
            brand_name=app_settings.PROJECT_NAME,
        ),
    )