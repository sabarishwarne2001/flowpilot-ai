"""
Delivery surface for the ARCH-04 invitation lifecycle.

Six messages: the invitation to the invitee, acceptance and rejection notices
to the inviter, a revocation notice to the invitee, a seat-exhaustion notice to
the inviter, and an expiry digest to the inviter.

Deliberately not part of NotificationService (Step 1 D1.3):

  - send_notification writes a row to `notifications`, where workspace_id is
    NOT NULL and every read is workspace-scoped by the ARCH-02 isolation suite.
    An organization-scoped invitation event has no workspace, and a zero-grant
    invitation has none even in principle. Supplying an arbitrary one files an
    organization event into a workspace timeline the recipient may not read.
  - send_notification is async. The Step 8 sweeper is a synchronous script.
  - notification_service imports app.crud, which pulls the model graph. This
    module takes no Session and imports no model, so a template test needs no
    database and the sweeper needs no application context.
  - Taking no Session means this cannot inherit the background-task hazard in
    the ARCH-03 register path, where a request-scoped Session is handed to a
    BackgroundTask and its validity depends on dependency-teardown ordering.

Every function takes primitives, not ORM objects. Step 2 is what creates
the organization invitation database models; this module is written and tested
before it exists, and keeping it that way means Step 6's service stays the only
place that knows the model -- which is where the transaction and the seat check
live anyway.

Every function returns bool and raises nothing. Both reasons are load-bearing
for later steps:

  - At issuance, an invitation whose email fails must still exist. There is a
    resend endpoint (Step 7) and a send_count column (Step 2) precisely so a
    failed send is recoverable. ARCH-03 R7 applied to a new surface.
  - The accept, reject and seat-blocked notices are courtesy mail sent *after*
    the transaction resolves, never inside it. A membership must not roll back
    because a notification to a third party bounced. Step 6 calls these after
    commit_and_refresh, not before.

No function logs a subject, a body, or a link. The accept link carries a live
credential and an application log is not a secret store (ARCH-03 R4).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Callable, Sequence

from app.core.config import settings as app_settings
from app.core.platform_email import (
    PlatformEmailNotConfigured,
    send_platform_email,
)
from app.templates.emails.common import ExpiredInvitationLine, GrantLine
from app.templates.emails.invitation_accepted import render_invitation_accepted
from app.templates.emails.invitation_expiry_digest import (
    render_invitation_expiry_digest,
)
from app.templates.emails.invitation_rejected import render_invitation_rejected
from app.templates.emails.invitation_revoked import render_invitation_revoked
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
    """
    Renders and delivers one message, converting every failure into False.
    """
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


# ==========================================================================
# To the invitee
# ==========================================================================

def send_invitation(
    *,
    invited_email: str,
    organization_name: str,
    inviter_email: str,
    organization_role_display: str,
    grants: Sequence[GrantLine],
    accept_link: str,
    expires_at: datetime,
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
            inviter_display=inviter_email,
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
    invitation_id: uuid.UUID | None = None,
) -> bool:
    return _send(
        event="INVITATION_REVOKED",
        recipient=invited_email,
        invitation_id=invitation_id,
        render=lambda: render_invitation_revoked(
            invited_email=invited_email,
            organization_name=organization_name,
            inviter_display=inviter_email,
            brand_name=app_settings.PROJECT_NAME,
        ),
    )


# ==========================================================================
# To the inviter
# ==========================================================================

def send_invitation_accepted(
    *,
    inviter_email: str,
    invited_email: str,
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


def send_expiry_digest(
    *,
    inviter_email: str,
    lines: Sequence[ExpiredInvitationLine],
    invitations_url: str,
) -> bool:
    if not lines:
        return False

    return _send(
        event="INVITATION_EXPIRY_DIGEST",
        recipient=inviter_email,
        render=lambda: render_invitation_expiry_digest(
            lines=lines,
            invitations_url=invitations_url,
            brand_name=app_settings.PROJECT_NAME,
        ),
    )