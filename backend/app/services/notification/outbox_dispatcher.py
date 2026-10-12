"""ARCH-12 Step 7 — production notification delivery.

THE SPECIFIC THING TO GET RIGHT
===============================

**Notification content must pass the same output filter as the chat stream.**

A summary containing an identifier that the stream redacted, which then goes
out by email, defeats the filter entirely — and email is the channel that
leaves your perimeter. So `redact_text` from `output_filter` is applied here,
on the same rules the stream uses, and the redacted copy is what gets stored
in `notification_deliveries.payload`. Storing pre-redaction content and
filtering at send time would leave the unfiltered version in the database for
anyone with read access and for every future channel that forgets to filter.

DELIVERY VIA `jobs`, NOT A NEW MECHANISM
========================================

ARCH-09 Step 10 built a job queue with leases, attempt counting, dead-letter
and a claim index. This does not build a second one. `dispatch` writes
delivery rows and enqueues one `notification.deliver` job per row inside the
caller's transaction — the outbox discipline, so a notification is never
enqueued for something that rolled back.

ONE DELIVERY RECORD PER CHANNEL
===============================

`notifications.delivery_channel` can describe one channel. A user whose
preferences say in-app *and* email needs two independent attempt counters,
because the in-app write succeeds instantly and the email may spend an hour in
backoff. Those are two facts and they need two rows.

IN-APP IS DELIVERED SYNCHRONOUSLY
=================================

It is a database write in a transaction that is already open. Enqueuing a job
to do it would add queue latency to the one channel whose entire value is that
the badge appears immediately.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationStatus,
)
from app.core.idempotent_insert import insert_or_get
from app.models.notification_delivery import (
    NotificationDelivery,
    NotificationDeliveryStatus,
)
from app.models.user import User
from app.services import job_service
from app.services.output_filter import RedactionTally, redact_text

logger = logging.getLogger("app.services.notification.outbox_dispatcher")

JOB_TYPE = "notification.deliver"

#: Channels that leave the perimeter. Used only for logging emphasis — the
#: filter is applied to every channel regardless, because "internal" channels
#: get forwarded.
EXTERNAL_CHANNELS: frozenset[NotificationChannel] = frozenset(
    {NotificationChannel.EMAIL, NotificationChannel.WEBHOOK}
)


@dataclass
class ChannelPreference:
    """Where one user wants one notification type delivered."""

    channel: NotificationChannel
    target: Optional[str] = None
    max_attempts: int = 6


@dataclass
class DispatchResult:
    notification_id: uuid.UUID
    deliveries: list[uuid.UUID] = field(default_factory=list)
    redactions: dict[str, int] = field(default_factory=dict)

    def as_details(self) -> dict[str, Any]:
        return {
            "notification_id": str(self.notification_id),
            "deliveries": len(self.deliveries),
            "redactions": self.redactions,
        }


def resolve_preferences(
    db: Session,
    *,
    user: User,
    notification: Notification,
    category: Optional[str] = None,
) -> list[ChannelPreference]:
    """Which channels this user gets this notification on.

    In-app always. Email by `notification_preferences_service.should_email`
    (campaign session 3, N-062): Security and Billing always; the optional
    categories for WARNING and ERROR only, and only while the person keeps them
    on. Escalating a routine DOCUMENT notification to email is how a product
    teaches its users to filter its mail to a folder they never read.
    """
    from app.services import notification_preferences_service as email_choices

    preferences = [ChannelPreference(channel=NotificationChannel.IN_APP)]

    key = category or email_choices.infer_category(notification.notification_type)
    if getattr(user, "email", None) and email_choices.should_email(
        db, user_id=user.id, category_key=key, priority=notification.priority
    ):
        preferences.append(
            ChannelPreference(channel=NotificationChannel.EMAIL, target=user.email)
        )

    return preferences


def _filtered_payload(
    *, title: str, message: str, tally: RedactionTally
) -> dict[str, Any]:
    """Apply the stream's output filter before anything is stored or sent."""
    return {
        "title": redact_text(title, tally=tally),
        "body": redact_text(message, tally=tally),
    }


def dispatch(
    db: Session,
    *,
    notification: Notification,
    user: User,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID] = None,
    preferences: Optional[Sequence[ChannelPreference]] = None,
    idempotency_prefix: Optional[str] = None,
    category: Optional[str] = None,
    action_path: Optional[str] = None,
    action_label: Optional[str] = None,
) -> DispatchResult:
    """Create one delivery per channel and enqueue the external ones.

    Must be called inside an open transaction. `job_service.enqueue` enforces
    that; the same requirement applies to the delivery rows themselves, which
    is why nothing here commits.

    `category` (a `notification_preferences_service` key; inferred when not
    given) decides the email rule and the relay; `action_path` is an in-product
    path the email links to (the frontend origin is added at send time).
    """
    from app.services import notification_preferences_service as email_choices

    key = category or email_choices.infer_category(notification.notification_type)
    resolved = list(
        preferences
        or resolve_preferences(db, user=user, notification=notification, category=key)
    )
    tally = RedactionTally()
    payload = _filtered_payload(
        title=notification.title, message=notification.message, tally=tally
    )
    payload["category"] = key
    if action_path:
        payload["action_path"] = action_path
        payload["action_label"] = action_label

    result = DispatchResult(notification_id=notification.id, redactions=dict(tally.counts))

    for preference in resolved:
        idempotency_key = (
            f"{idempotency_prefix or 'notify'}:{notification.id}:"
            f"{preference.channel.value}"
        )[:200]

        delivery = NotificationDelivery(
            notification_id=notification.id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            channel=preference.channel,
            target=preference.target,
            payload=payload,
            max_attempts=preference.max_attempts,
            idempotency_key=idempotency_key,
        )

        if preference.channel is NotificationChannel.IN_APP:
            # Already durable the moment this transaction commits.
            delivery.mark_delivered()
            notification.delivery_status = NotificationStatus.SENT

        # SEAM-I-2. uq_notification_deliveries_org_idempotency_key:
        # (organization_id, idempotency_key) WHERE idempotency_key IS NOT NULL.
        # The constraint was named for this and never given the handling.
        delivery, created = insert_or_get(
            db,
            instance=delivery,
            lookup=lambda: (
                None
                if not idempotency_key
                else db.execute(
                    select(NotificationDelivery)
                    .where(NotificationDelivery.organization_id == organization_id)
                    .where(NotificationDelivery.idempotency_key == idempotency_key)
                    .limit(1)
                ).scalar_one_or_none()
            ),
            label="notification.delivery",
            log_extra={
                "organization_id": str(organization_id),
                "idempotency_key": idempotency_key,
            },
        )
        result.deliveries.append(delivery.id)

        # `created` gates the enqueue. Without it, a replay returns the
        # existing delivery and then queues a SECOND send job for it, which
        # is how a retry turns into a duplicate email.
        if created and preference.channel is not NotificationChannel.IN_APP:
            from app.workers.handlers import ensure_registered

            ensure_registered(JOB_TYPE)
            job_service.enqueue(
                db,
                job_type=JOB_TYPE,
                payload={"delivery_id": str(delivery.id)},
                organization_id=organization_id,
                max_attempts=preference.max_attempts,
                idempotency_key=idempotency_key,
            )

    if tally.total:
        logger.warning(
            "notification.content_redacted",
            extra={
                "notification_id": str(notification.id),
                "channels": [preference.channel.value for preference in resolved],
                "external": [
                    preference.channel.value
                    for preference in resolved
                    if preference.channel in EXTERNAL_CHANNELS
                ],
                **tally.as_details(),
            },
        )

    logger.info("notification.dispatched", extra=result.as_details())
    return result


def claim_delivery(
    db: Session, *, delivery_id: uuid.UUID
) -> Optional[NotificationDelivery]:
    """Lock one delivery row for an attempt. None if already terminal."""
    delivery = db.execute(
        select(NotificationDelivery)
        .where(NotificationDelivery.id == delivery_id)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()

    if delivery is None:
        return None
    if delivery.is_terminal:
        logger.info(
            "notification.delivery_already_terminal",
            extra={"delivery_id": str(delivery_id), "status": delivery.status.value},
        )
        return None

    delivery.status = NotificationDeliveryStatus.SENDING
    db.flush([delivery])
    return delivery


async def send_delivery(db: Session, delivery: NotificationDelivery) -> bool:
    """Perform one attempt. Records success or schedules the next.

    Returns True on delivery. Never raises: a transport exception is a failed
    attempt, and dead-lettering after `max_attempts` is what turns a
    permanently broken endpoint into an operational signal instead of an
    infinite retry loop.
    """
    payload = delivery.payload or {}
    title = str(payload.get("title") or "")
    body = str(payload.get("body") or "")

    try:
        if delivery.channel is NotificationChannel.EMAIL:
            success = await _send_email(db, delivery, title=title, body=body)
        elif delivery.channel is NotificationChannel.WEBHOOK:
            success = await _send_webhook(db, delivery, title=title, body=body)
        else:
            raise ValueError(
                f"channel {delivery.channel} has no outbound transport; "
                "IN_APP is delivered synchronously by dispatch()."
            )
    except Exception as exc:  # noqa: BLE001
        delivery.mark_failed(f"{type(exc).__name__}: {exc}")
        logger.warning(
            "notification.delivery_attempt_failed",
            extra={
                "delivery_id": str(delivery.id),
                "channel": delivery.channel.value,
                "attempts": delivery.attempts,
                "status": delivery.status.value,
            },
            exc_info=True,
        )
        return False

    if success:
        delivery.mark_delivered()
        logger.info(
            "notification.delivered",
            extra={
                "delivery_id": str(delivery.id),
                "channel": delivery.channel.value,
                "attempts": delivery.attempts + 1,
            },
        )
        return True

    delivery.mark_failed("transport reported failure")
    if delivery.status is NotificationDeliveryStatus.DEAD:
        logger.error(
            "notification.dead_lettered",
            extra={
                "delivery_id": str(delivery.id),
                "channel": delivery.channel.value,
                "target": delivery.target,
                "attempts": delivery.attempts,
            },
        )
    return False


async def _send_email(
    db: Session, delivery: NotificationDelivery, *, title: str, body: str
) -> bool:
    from app.core.config import settings as app_settings
    from app.core.platform_email import platform_smtp_config
    from app.core.smtp import resolve_smtp_config
    from app.models.organization import Organization
    from app.services import notification_preferences_service as email_choices
    from app.services.notification.dispatcher import notification_dispatcher
    from app.templates.emails.account_notice import render_account_notice

    if not delivery.target:
        raise ValueError("email delivery has no target address")

    payload = delivery.payload or {}
    category = email_choices.category(
        str(payload.get("category") or email_choices.WORKFLOW)
    )

    if category.essential:
        # Campaign session 3 (N-062). Security and billing mail is FlowPilot
        # writing to a person about their account, like a password reset: it
        # goes through the platform relay, never a tenant's own SMTP, which an
        # administrator can misconfigure (or point elsewhere) and which is
        # exactly what is in question when the notice is about that tenant.
        # Not configured: this raises, the attempt fails, and it is retried.
        smtp = platform_smtp_config()
    else:
        # ARCH40-S1:dispatcher-org-tier. `organization_id` was omitted here and
        # in notification_service, so the organization email settings tier was
        # unreachable from every email the product actually sent. Passing it
        # makes the ladder in `email_resolution` real rather than notional.
        smtp = resolve_smtp_config(
            db,
            workspace_id=delivery.workspace_id,
            organization_id=getattr(delivery, "organization_id", None),
        )

    organization = (
        db.get(Organization, delivery.organization_id)
        if delivery.organization_id
        else None
    )
    frontend = app_settings.FRONTEND_URL.rstrip("/")
    action_path = payload.get("action_path")
    subject, html_body, text_body = render_account_notice(
        title=title,
        body=body,
        organization_name=organization.name if organization is not None else None,
        category_label=category.label,
        essential=category.essential,
        action_url=f"{frontend}{action_path}" if action_path else None,
        action_label=payload.get("action_label"),
        preferences_url=f"{frontend}/settings?section=notifications",
        brand_name=app_settings.PROJECT_NAME,
    )
    return await notification_dispatcher.send(
        action_type="email",
        settings=smtp,
        recipient=delivery.target,
        title=subject,
        body=text_body,
        html_body=html_body,
    )


async def _send_webhook(
    db: Session, delivery: NotificationDelivery, *, title: str, body: str
) -> bool:
    """Hand off to ARCH-09's webhook path rather than posting directly.

    Signing, SSRF policy, and the per-endpoint circuit breaker all live there.
    A second HTTP caller here would be a second place to get those wrong.
    """
    from app.services import webhook_service

    emit = getattr(webhook_service, "deliver_notification", None)
    if emit is None:
        raise NotImplementedError(
            "webhook notification delivery requires ARCH-13's "
            "webhook_service.deliver_notification. The delivery row is "
            "created and will dead-letter rather than silently disappearing."
        )
    return bool(
        await emit(db, delivery_id=delivery.id, title=title, body=body)
    )


def dead_letters(
    db: Session, *, organization_id: uuid.UUID, limit: int = 100
) -> list[NotificationDelivery]:
    """Operational read path. Served by `ix_notification_deliveries_dead`."""
    return list(
        db.execute(
            select(NotificationDelivery)
            .where(
                NotificationDelivery.organization_id == organization_id,
                NotificationDelivery.status == NotificationDeliveryStatus.DEAD,
            )
            .order_by(NotificationDelivery.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


__all__ = [
    "ChannelPreference",
    "DispatchResult",
    "EXTERNAL_CHANNELS",
    "JOB_TYPE",
    "claim_delivery",
    "dead_letters",
    "dispatch",
    "resolve_preferences",
    "send_delivery",
]
