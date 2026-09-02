"""Gate 12.7 — multi-channel delivery, backoff, dead-lettering, and the filter.

The assertion this suite exists for is the last one:

    a summary containing an identifier the chat stream redacted must not
    leave the perimeter by email

Everything else — per-channel rows, capped exponential backoff, a terminal
DEAD state — is retry mechanics that `jobs` already established. The filter is
the thing that is new and the thing that is easy to get wrong, because the
notification path and the chat path are written months apart by different
reasoning.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
    NotificationType,
)
from app.models.notification_delivery import (
    BACKOFF_CAP_SECONDS,
    NotificationDelivery,
    NotificationDeliveryStatus,
    backoff_delay,
)
from app.services.notification import outbox_dispatcher
from app.services.output_filter import REDACTION
from app.workers.handlers import register_all


@pytest.fixture(autouse=True)
def _register_job_handlers():
    """Ensure notification.deliver handler is registered before test dispatch."""
    register_all(replace=True)


@pytest.fixture()
def notification(db_session, tenant) -> Notification:
    row = Notification(
        title="Document processed",
        message=(
            "Summary: the payslip for ana@acme.io lists national insurance "
            "number QQ123456C and account 4111 1111 1111 1111."
        ),
        notification_type=NotificationType.DOCUMENT,
        priority=NotificationPriority.WARNING,
        delivery_channel=NotificationChannel.IN_APP,
        delivery_status=NotificationStatus.PENDING,
        workspace_id=tenant.workspace.id,
        organization_id=tenant.organization.id,
        user_id=tenant.contributor.user.id,
    )
    db_session.add(row)
    db_session.commit()
    return row


# ===========================================================================
# The gate
# ===========================================================================


def test_email_content_passes_the_same_filter_as_the_chat_stream(
    db_session, tenant, notification
):
    """A redacted-in-chat identifier must not go out by email."""
    with db_session.begin_nested():
        result = outbox_dispatcher.dispatch(
            db_session,
            notification=notification,
            user=tenant.contributor.user,
            organization_id=tenant.organization.id,
            workspace_id=tenant.workspace.id,
        )

    deliveries = [db_session.get(NotificationDelivery, id_) for id_ in result.deliveries]
    email = next(d for d in deliveries if d.channel is NotificationChannel.EMAIL)

    body = email.payload["body"]
    assert "ana@acme.io" not in body
    assert "QQ123456C" not in body
    assert "4111 1111 1111 1111" not in body
    assert REDACTION in body

    assert result.redactions, "the redaction must be reported, not silent"


def test_the_stored_copy_is_already_redacted(db_session, tenant, notification):
    """Filtering at send time leaves the raw text in the database."""
    with db_session.begin_nested():
        result = outbox_dispatcher.dispatch(
            db_session,
            notification=notification,
            user=tenant.contributor.user,
            organization_id=tenant.organization.id,
            workspace_id=tenant.workspace.id,
        )

    for delivery_id in result.deliveries:
        delivery = db_session.get(NotificationDelivery, delivery_id)
        assert "QQ123456C" not in str(delivery.payload)


# ===========================================================================
# Per-channel rows
# ===========================================================================


def test_one_delivery_row_per_channel(db_session, tenant, notification):
    with db_session.begin_nested():
        result = outbox_dispatcher.dispatch(
            db_session,
            notification=notification,
            user=tenant.contributor.user,
            organization_id=tenant.organization.id,
            workspace_id=tenant.workspace.id,
        )

    channels = {
        db_session.get(NotificationDelivery, id_).channel for id_ in result.deliveries
    }
    assert channels == {NotificationChannel.IN_APP, NotificationChannel.EMAIL}


def test_in_app_is_delivered_synchronously(db_session, tenant, notification):
    with db_session.begin_nested():
        result = outbox_dispatcher.dispatch(
            db_session,
            notification=notification,
            user=tenant.contributor.user,
            organization_id=tenant.organization.id,
            workspace_id=tenant.workspace.id,
        )

    in_app = next(
        d
        for d in (db_session.get(NotificationDelivery, i) for i in result.deliveries)
        if d.channel is NotificationChannel.IN_APP
    )
    assert in_app.status is NotificationDeliveryStatus.DELIVERED
    assert in_app.delivered_at is not None


def test_routine_priority_does_not_escalate_to_email(db_session, tenant, notification):
    notification.priority = NotificationPriority.INFO
    db_session.flush()

    preferences = outbox_dispatcher.resolve_preferences(
        db_session, user=tenant.contributor.user, notification=notification
    )
    assert [p.channel for p in preferences] == [NotificationChannel.IN_APP]


# ===========================================================================
# Retry mechanics
# ===========================================================================


def test_backoff_is_exponential_and_capped():
    delays = [backoff_delay(attempt).total_seconds() for attempt in range(0, 10)]

    assert delays[0] < delays[1] < delays[2]
    assert delays[1] == pytest.approx(delays[0] * 2)
    assert max(delays) == BACKOFF_CAP_SECONDS
    assert all(delay <= BACKOFF_CAP_SECONDS for delay in delays)


def test_failure_schedules_a_later_attempt():
    delivery = NotificationDelivery(
        notification_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        channel=NotificationChannel.EMAIL,
        status=NotificationDeliveryStatus.SENDING,
        attempts=0,
        max_attempts=6,
        next_attempt_at=datetime.now(timezone.utc),
        payload={},
    )

    delivery.mark_failed("smtp 421 service not available")

    assert delivery.status is NotificationDeliveryStatus.FAILED
    assert delivery.attempts == 1
    assert delivery.next_attempt_at > datetime.now(timezone.utc)
    assert "421" in delivery.last_error


def test_dead_letter_after_max_attempts():
    delivery = NotificationDelivery(
        notification_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        channel=NotificationChannel.EMAIL,
        status=NotificationDeliveryStatus.SENDING,
        attempts=5,
        max_attempts=6,
        next_attempt_at=datetime.now(timezone.utc),
        payload={},
    )

    delivery.mark_failed("host unreachable")

    assert delivery.status is NotificationDeliveryStatus.DEAD
    assert delivery.is_terminal
    assert delivery.attempts == 6


def test_terminal_deliveries_are_not_reclaimed(db_session, tenant, notification):
    delivery = NotificationDelivery(
        notification_id=notification.id,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        channel=NotificationChannel.EMAIL,
        status=NotificationDeliveryStatus.DEAD,
        attempts=6,
        max_attempts=6,
        next_attempt_at=datetime.now(timezone.utc) - timedelta(hours=1),
        payload={"title": "t", "body": "b"},
    )
    db_session.add(delivery)
    db_session.commit()

    assert outbox_dispatcher.claim_delivery(db_session, delivery_id=delivery.id) is None


def test_dead_letters_are_queryable_per_organization(db_session, tenant, notification):
    for index in range(3):
        db_session.add(
            NotificationDelivery(
                notification_id=notification.id,
                organization_id=tenant.organization.id,
                channel=NotificationChannel.EMAIL,
                status=NotificationDeliveryStatus.DEAD,
                attempts=6,
                max_attempts=6,
                next_attempt_at=datetime.now(timezone.utc),
                payload={"title": f"t{index}", "body": "b"},
            )
        )
    db_session.commit()

    found = outbox_dispatcher.dead_letters(
        db_session, organization_id=tenant.organization.id
    )
    assert len(found) >= 3
    assert all(d.status is NotificationDeliveryStatus.DEAD for d in found)


def test_dispatch_is_idempotent_per_channel(db_session, tenant, notification):
    from sqlalchemy.exc import IntegrityError

    with db_session.begin_nested():
        outbox_dispatcher.dispatch(
            db_session,
            notification=notification,
            user=tenant.contributor.user,
            organization_id=tenant.organization.id,
            workspace_id=tenant.workspace.id,
        )

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            outbox_dispatcher.dispatch(
                db_session,
                notification=notification,
                user=tenant.contributor.user,
                organization_id=tenant.organization.id,
                workspace_id=tenant.workspace.id,
            )
