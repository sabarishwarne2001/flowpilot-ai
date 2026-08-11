"""
Delivery surface for the ARCH-05 ownership transfer lifecycle.

Four events, five messages: the proposal to the target, the completion notice
to BOTH parties, the decline notice to the initiator, the cancellation notice
to the target.

Mirrors invitation_mail.py deliberately and almost line for line. That module
already argued for this shape at ARCH-04 Step 1 and every reason it gave still
applies here; a second module with a different shape would mean two mail
conventions to keep in your head, and the boundary tests would have to be
written twice.

Restated because the reasons are load-bearing rather than stylistic:

  - NOT part of NotificationService. send_notification writes a row to
    `notifications`, where workspace_id is NOT NULL and every read is
    workspace-scoped by the ARCH-02 isolation suite. Ownership is an
    organization-level fact with no workspace at all, and supplying an
    arbitrary one files it into a timeline the recipient may not even read.
    (ARCH-06 adds notifications.organization_id; until then, this.)
  - NO Session, no app.crud import, no ORM import. Every function takes
    primitives. Step 3 is what creates the ownership_transfers model; this
    module is written and tested before it exists, which is what keeps Step 6's
    service the only place that knows the model — and the transaction.
  - Taking no Session also means these cannot inherit the ARCH-03 background
    task hazard, where a request-scoped Session handed to a BackgroundTask
    stays valid only by dependency-teardown ordering.
  - Every function returns bool and raises nothing. These are courtesy notices
    dispatched from post-commit carriers (§B.7). An ownership transfer must not
    roll back because a notification bounced — the transfer is the thing the
    two parties agreed to; the email is how they find out it happened.

WHAT THIS MODULE MUST NEVER DO
    Log a subject, a body, or a link. ARCH-03 R4. The proposal link here is not
    a credential (§B.1: no token, acceptance is in-app and re-authorized on
    arrival), so this is a weaker rule for ownership than it was for
    invitations — but a shared _send that logs bodies for one caller and not
    another is a rule nobody can apply, so the rule stays absolute.

THE §B.6 TRAP, WHICH THIS MODULE IS WHERE IT GETS AVOIDED
    Every send function below takes *_email and *_display as separate
    arguments, and passes both down. The templates put *_email in every
    `mailto:` href and *_display in prose. ARCH-04's invitation_revoked.py was
    written with one `inviter_display` argument used for both, on the correct
    assumption that `users` had no name column; once ARCH-05 Step 3 adds
    display_name, passing a name into that href produces `mailto:Jane Smith` —
    a dead link in a message whose entire purpose is telling someone who to
    contact. Splitting the parameter here from the first commit means these
    four templates never acquire the debt that Step 8 has to pay off in that
    one.

    Callers with a NULL display_name should pass the address for BOTH. That is
    §B.4's reasoning applied at the boundary: a NULL rendering as the email
    address is honest, and deriving "jane" from "jane@…" is a guess the product
    would then treat as a fact.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Callable

from app.core.config import settings as app_settings
from app.core.platform_email import (
    PlatformEmailNotConfigured,
    send_platform_email,
)
from app.templates.emails.ownership_transfer_cancelled import (
    render_ownership_transfer_cancelled,
)
from app.templates.emails.ownership_transfer_declined import (
    render_ownership_transfer_declined,
)
from app.templates.emails.ownership_transfer_requested import (
    render_ownership_transfer_requested,
)
from app.templates.emails.ownership_transferred import (
    render_ownership_transferred,
)

logger = logging.getLogger("app.services.ownership_mail")

#: Rendered message: (subject, html_body, text_body).
RenderedMessage = tuple[str, str, str]


def _support_email() -> str:
    """
    Where a recipient who did not expect a transfer should write.

    The platform From address, matching what ARCH-03 Step 9 passes to
    render_password_changed. Read at call time rather than at import so a
    settings override in a test or a smoke script takes effect.
    """
    return app_settings.PLATFORM_SMTP_FROM_EMAIL


def _send(
    *,
    event: str,
    recipient: str,
    render: Callable[[], RenderedMessage],
    transfer_id: uuid.UUID | None = None,
    reply_to: str | None = None,
) -> bool:
    """
    Renders and delivers one message, converting every failure into False.

    Render failures are caught separately from delivery failures because they
    are different bugs with different fixes: a render failure is ours (a naive
    datetime reaching format_timestamp, a perspective typo), a delivery failure
    is usually the relay's. Collapsing both into one log line would mean the
    first class of bug hid inside the second.
    """
    try:
        subject, html_body, text_body = render()
    except Exception:
        logger.exception(
            "OWNERSHIP_MAIL_RENDER_FAILED | event=%s | transfer=%s",
            event,
            transfer_id,
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
            "OWNERSHIP_MAIL_UNCONFIGURED | event=%s | transfer=%s | %s",
            event,
            transfer_id,
            exc,
        )
        return False
    except Exception:
        logger.exception(
            "OWNERSHIP_MAIL_ERROR | event=%s | transfer=%s",
            event,
            transfer_id,
        )
        return False

    if delivered:
        logger.info(
            "OWNERSHIP_MAIL_SENT | event=%s | transfer=%s | to=%s",
            event,
            transfer_id,
            recipient,
        )
    else:
        logger.warning(
            "OWNERSHIP_MAIL_FAILED | event=%s | transfer=%s | to=%s | %s",
            event,
            transfer_id,
            recipient,
            detail,
        )

    return delivered


# ==========================================================================
# To the target
# ==========================================================================

def send_transfer_requested(
    *,
    target_email: str,
    organization_name: str,
    initiator_email: str,
    initiator_display: str,
    review_link: str,
    expires_at: datetime,
    transfer_id: uuid.UUID | None = None,
) -> bool:
    """
    The proposal itself (§B.7).

    Reply-To is the initiator, matching send_invitation. A target who wants to
    ask "why me?" before deciding should be able to hit reply and reach a
    person, not the platform's no-reply mailbox.
    """
    return _send(
        event="OWNERSHIP_TRANSFER_REQUESTED",
        recipient=target_email,
        transfer_id=transfer_id,
        reply_to=initiator_email,
        render=lambda: render_ownership_transfer_requested(
            recipient_email=target_email,
            organization_name=organization_name,
            initiator_email=initiator_email,
            initiator_display=initiator_display,
            review_link=review_link,
            expires_at=expires_at,
            brand_name=app_settings.PROJECT_NAME,
        ),
    )


def send_transfer_cancelled(
    *,
    target_email: str,
    organization_name: str,
    initiator_email: str,
    initiator_display: str,
    cancelled_at: datetime,
    transfer_id: uuid.UUID | None = None,
) -> bool:
    """Withdraws a proposal the target may be about to accept (§B.7)."""
    return _send(
        event="OWNERSHIP_TRANSFER_CANCELLED",
        recipient=target_email,
        transfer_id=transfer_id,
        reply_to=initiator_email,
        render=lambda: render_ownership_transfer_cancelled(
            recipient_email=target_email,
            organization_name=organization_name,
            initiator_email=initiator_email,
            initiator_display=initiator_display,
            cancelled_at=cancelled_at,
            brand_name=app_settings.PROJECT_NAME,
        ),
    )


# ==========================================================================
# To the initiator
# ==========================================================================

def send_transfer_declined(
    *,
    initiator_email: str,
    organization_name: str,
    target_email: str,
    target_display: str,
    declined_at: datetime,
    transfer_id: uuid.UUID | None = None,
) -> bool:
    """
    Otherwise a declined proposal looks identical to an ignored one (§B.7).

    Reply-To is the person who declined, so "can we talk about this?" is one
    keystroke rather than a search through the member directory.
    """
    return _send(
        event="OWNERSHIP_TRANSFER_DECLINED",
        recipient=initiator_email,
        transfer_id=transfer_id,
        reply_to=target_email,
        render=lambda: render_ownership_transfer_declined(
            recipient_email=initiator_email,
            organization_name=organization_name,
            target_email=target_email,
            target_display=target_display,
            declined_at=declined_at,
            brand_name=app_settings.PROJECT_NAME,
        ),
    )


# ==========================================================================
# To both parties — the A.2.2 fix
# ==========================================================================

def send_ownership_transferred(
    *,
    organization_name: str,
    previous_owner_email: str,
    previous_owner_display: str,
    new_owner_email: str,
    new_owner_display: str,
    transferred_at: datetime,
    transfer_id: uuid.UUID | None = None,
) -> tuple[bool, bool]:
    """
    Notifies both parties that ownership has changed hands.

    Returns:
        (delivered_to_previous_owner, delivered_to_new_owner).

    Two return values rather than one aggregate bool, and the order is not
    arbitrary. The notice to the OUTGOING owner is the one that matters
    (A.2.2): it is the only signal that a transfer they did not intend has
    completed, on the same reasoning ARCH-03 gives for sending
    password-changed on every change. A single `bool` would let that failure
    hide behind the new owner's successful delivery, and the new owner already
    knows — they clicked accept.

    Neither send is attempted inside the transaction. Both are dispatched from
    a post-commit carrier via BackgroundTasks (§B.7), so a bounced notice
    cannot roll back a transfer two people agreed to.

    No reply_to on either message. These are security notices, and pointing
    "reply" at the counterparty of a transfer the reader may be about to
    dispute is the wrong default; the body names support_email instead.
    """
    support = _support_email()
    brand = app_settings.PROJECT_NAME

    to_previous = _send(
        event="OWNERSHIP_TRANSFERRED_OUTGOING",
        recipient=previous_owner_email,
        transfer_id=transfer_id,
        render=lambda: render_ownership_transferred(
            recipient_email=previous_owner_email,
            perspective="outgoing",
            organization_name=organization_name,
            previous_owner_email=previous_owner_email,
            previous_owner_display=previous_owner_display,
            new_owner_email=new_owner_email,
            new_owner_display=new_owner_display,
            transferred_at=transferred_at,
            brand_name=brand,
            support_email=support,
        ),
    )

    to_new = _send(
        event="OWNERSHIP_TRANSFERRED_INCOMING",
        recipient=new_owner_email,
        transfer_id=transfer_id,
        render=lambda: render_ownership_transferred(
            recipient_email=new_owner_email,
            perspective="incoming",
            organization_name=organization_name,
            previous_owner_email=previous_owner_email,
            previous_owner_display=previous_owner_display,
            new_owner_email=new_owner_email,
            new_owner_display=new_owner_display,
            transferred_at=transferred_at,
            brand_name=brand,
            support_email=support,
        ),
    )

    if not to_previous:
        # Escalated above the per-message warning _send already emitted. This
        # is the A.2.2 signal failing to reach the one person who might not
        # know the transfer happened.
        logger.error(
            "OWNERSHIP_NOTICE_UNDELIVERED | transfer=%s | to=outgoing_owner | "
            "the only signal an unintended transfer would have produced",
            transfer_id,
        )

    return to_previous, to_new