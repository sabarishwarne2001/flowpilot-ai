"""The deployment's owner is told when something breaks (campaign session 4, §C.4).

Before: a job that failed every attempt sat in the dead-letter queue until someone opened
/admin/jobs; a payment event that could not be applied dead-lettered the same way; a burst of
server errors was in a log file; and a second gateway subscription from an old checkout tab
violated the one-live-subscription index on every attempt and died, while the customer was
charged for it. Nobody was emailed about any of them.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select, text

from app.core.config import settings
from app.models.operator_alert import OperatorAlert
from app.services import operator_alert_service
from app.services.billing import reconcile_service
from app.services.billing.stripe_gateway import StripeEvent

from tests.services.test_arch15_gate_15_1_15_2_inbound import (  # noqa: F401
    FakeStripeGateway,
    billing_org,
    gateway,
    make_subscription,
    stripe_settings,
)
from tests.services.test_arch15_gate_15_3_15_4_subscriptions_seats import install_subscription


@pytest.fixture()
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def capture(**kwargs: Any) -> bool:
        sent.append(kwargs)
        return True

    monkeypatch.setattr(operator_alert_service, "_send", capture)
    monkeypatch.setattr(settings, "OPERATOR_ALERT_EMAILS", "ops@example.com, founder@example.org")
    monkeypatch.setattr(settings, "OPERATOR_ALERT_REPEAT_MINUTES", 60)
    monkeypatch.setattr(settings, "OPERATOR_ALERT_5XX_THRESHOLD", 20)
    return sent


def _alerts(db, kind: str) -> list[OperatorAlert]:
    return list(db.execute(select(OperatorAlert).where(OperatorAlert.kind == kind)).scalars())


def _dead_job(db, job_type: str, error: str, at: datetime) -> None:
    db.execute(text(
        "INSERT INTO jobs (id, job_type, payload, status, attempts, max_attempts, last_error, available_at, "
        "created_at, updated_at) VALUES (:id, :t, '{}'::jsonb, 'DEAD', 5, 5, :e, :at, :at, :at)"
    ), {"id": uuid.uuid4(), "t": job_type, "e": error, "at": at})


def test_a_second_live_subscription_is_not_written_and_the_owner_is_told_at_once(
    db, gateway, billing_org, outbox  # noqa: F811
) -> None:
    recorded = install_subscription(db, billing_org, gateway, seats=2)
    gateway.current["sub_old_tab"] = make_subscription(subscription_id="sub_old_tab", seats=2)
    event = StripeEvent(id="evt_dup", type="customer.subscription.created", created=datetime.now(timezone.utc),
                        livemode=False, api_version=None,
                        payload={"data": {"object": {"id": "sub_old_tab", "object": "subscription"}}})

    outcome = reconcile_service.reconcile_event(db, event)

    assert outcome.handled is False and outcome.ignored_reason == "duplicate_live_subscription", outcome
    db.refresh(recorded)
    assert recorded.stripe_subscription_id == "sub_gate15" and recorded.seats_purchased == 2
    [alert] = _alerts(db, "DUPLICATE_SUBSCRIPTION")
    assert "sub_old_tab" in alert.body and "sub_gate15" in alert.body and "9.8" in alert.body
    queued = db.execute(text("SELECT count(*) FROM jobs WHERE job_type = 'ops.alerts'")).scalar_one()
    assert queued == 1, "the alert goes out now, not at the next five-minute sweep"

    assert operator_alert_service.send_due(db) == 1
    assert sorted(m["recipient"] for m in outbox) == ["founder@example.org", "ops@example.com"]
    assert "charged twice" in outbox[0]["subject"]


def test_dead_jobs_are_emailed_once_and_again_only_when_more_die_after_an_hour(db, outbox) -> None:
    now = datetime.now(timezone.utc)
    _dead_job(db, "erp.deliver_posting", "ConnectionError: target refused", now - timedelta(minutes=3))
    _dead_job(db, "erp.deliver_posting", "ConnectionError: target refused", now - timedelta(minutes=2))

    operator_alert_service.scan(db, now=now)
    assert operator_alert_service.send_due(db, now=now) == 1
    assert len(outbox) == 2  # both recipients
    assert "2 background jobs failed for good: erp.deliver_posting" in outbox[0]["subject"]
    assert "target refused" in outbox[0]["text_body"]

    # Nothing new: no second email.
    operator_alert_service.scan(db, now=now + timedelta(minutes=5))
    assert operator_alert_service.send_due(db, now=now + timedelta(minutes=5)) == 0

    # Another one dies ten minutes later: still inside the hour, held back.
    _dead_job(db, "erp.deliver_posting", "ConnectionError: target refused", now + timedelta(minutes=10))
    operator_alert_service.scan(db, now=now + timedelta(minutes=11))
    assert operator_alert_service.send_due(db, now=now + timedelta(minutes=11)) == 0
    # After the hour it goes out, with the new count.
    operator_alert_service.scan(db, now=now + timedelta(minutes=65))
    assert operator_alert_service.send_due(db, now=now + timedelta(minutes=65)) == 1
    assert "3 background jobs" in outbox[-1]["subject"]


def test_a_burst_of_server_errors_is_an_alert_and_a_trickle_is_not(db, outbox) -> None:
    now = datetime.now(timezone.utc).replace(second=30, microsecond=0)
    minute = now.replace(second=0)
    db.execute(text("INSERT INTO server_error_minutes (minute, count) VALUES (:m, 7)"), {"m": minute})
    operator_alert_service.scan(db, now=now)
    assert _alerts(db, "SERVER_ERRORS") == []

    db.execute(text("UPDATE server_error_minutes SET count = 25 WHERE minute = :m"), {"m": minute})
    operator_alert_service.scan(db, now=now)
    [alert] = _alerts(db, "SERVER_ERRORS")
    assert alert.occurrences == 25
    assert operator_alert_service.send_due(db, now=now) == 1
    assert "25 requests" in outbox[0]["subject"]


def test_with_nobody_to_tell_the_alert_waits_and_is_sent_once_someone_is_named(
    db, outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "OPERATOR_ALERT_EMAILS", "")
    now = datetime.now(timezone.utc)
    _dead_job(db, "notification.deliver", "SMTP refused", now)
    operator_alert_service.scan(db, now=now)
    assert operator_alert_service.send_due(db, now=now) == 0 and outbox == []

    monkeypatch.setattr(settings, "OPERATOR_ALERT_EMAILS", "ops@example.com")
    assert operator_alert_service.send_due(db, now=now) == 1


def test_the_sweep_is_scheduled_on_a_profile_production_runs() -> None:
    from app.workers.handlers import _HANDLERS
    from app.workers.profiles import LIGHT
    from app.workers.scheduler import DEFAULT_SCHEDULE

    assert "ops.alerts" in _HANDLERS
    assert any(job.job_type == "ops.alerts" and job.interval_seconds <= 300 for job in DEFAULT_SCHEDULE)
    assert LIGHT.job_types is not None and "ops.alerts" in LIGHT.job_types


def test_a_5xx_answer_is_counted_and_reaches_the_shared_table(db) -> None:
    from app.core import ops_signals

    ops_signals.drain()
    at = datetime(2026, 10, 13, 9, 15, 42, tzinfo=timezone.utc)
    for _ in range(3):
        ops_signals.record_server_error(at)
    assert ops_signals.flush(db) == 1
    ops_signals.record_server_error(at)
    ops_signals.flush(db)
    count = db.execute(text("SELECT count FROM server_error_minutes WHERE minute = :m"),
                       {"m": at.replace(second=0)}).scalar_one()
    assert count == 4
