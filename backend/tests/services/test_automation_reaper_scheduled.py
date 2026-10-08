"""An automation run stranded in RUNNING is reaped by a scheduled job (F-146).

`executor.reap_stranded` turns a RUNNING execution past its deadline into
TIMED_OUT; it is what keeps a run whose worker was killed mid-walk from showing
"Running" in Run history forever. It had a test of its own and no caller: no job,
no schedule, nothing in the worker ran it, so a stranded run stayed RUNNING. The
ten-minute `pipeline.sweep_stuck` job (light profile, the same one that fails
stranded documents) now runs it too.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.db.session as session_module
from app.models.automation_execution import AutomationExecution, AutomationExecutionStatus
from app.workers.dead_letter import handle_pipeline_sweep_stuck
from tests.conftest import TestSessionLocal


def _execution(db, tenant, rule, *, deadline: datetime) -> AutomationExecution:
    execution = AutomationExecution(
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        rule_id=rule.id,
        correlation_id=uuid.uuid4(),
        depth=0,
        status=AutomationExecutionStatus.RUNNING,
        budget_cost_micros=50_000,
        started_at=deadline - timedelta(minutes=5),
        deadline_at=deadline,
    )
    db.add(execution)
    db.flush()
    return execution


def test_the_scheduled_sweep_reaps_a_stranded_run_and_spares_a_live_one(
    db_session, tenant, rule_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "SessionLocal", TestSessionLocal)
    rule = rule_factory(name="stranded-by-a-killed-worker")
    now = datetime.now(timezone.utc)
    stranded = _execution(db_session, tenant, rule, deadline=now - timedelta(minutes=20))
    live = _execution(db_session, tenant, rule, deadline=now + timedelta(minutes=5))
    db_session.commit()

    result = handle_pipeline_sweep_stuck({})

    assert result["automation_runs_reaped"] == 1
    db_session.expire_all()
    assert db_session.get(AutomationExecution, stranded.id).status is AutomationExecutionStatus.TIMED_OUT
    assert db_session.get(AutomationExecution, live.id).status is AutomationExecutionStatus.RUNNING


def test_the_scheduled_sweep_requeues_a_stranded_notification_delivery(
    db_session, tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-147. `sweep_due_deliveries` re-enqueues a FAILED delivery that is due and has no job
    (its job died, e.g. on lease expiries that used up its attempts). Its docstring said it ran
    "on the same schedule as reap_expired_leases"; nothing ran it, so such an email was never
    retried. The scheduled sweep now runs it."""
    from sqlalchemy import select

    from app.models.job import Job
    from app.models.notification import (
        Notification,
        NotificationChannel,
        NotificationPriority,
        NotificationStatus,
        NotificationType,
    )
    from app.models.notification_delivery import NotificationDelivery, NotificationDeliveryStatus
    from app.services.notification import outbox_dispatcher
    from app.workers.handlers import notify, register_all

    register_all()  # as the worker does at start; enqueue refuses an unregistered job type
    monkeypatch.setattr(session_module, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(notify, "SessionLocal", TestSessionLocal)
    notification = Notification(
        title="Document failed",
        message="A document could not be processed.",
        notification_type=NotificationType.DOCUMENT,
        priority=NotificationPriority.WARNING,
        delivery_channel=NotificationChannel.IN_APP,
        delivery_status=NotificationStatus.PENDING,
        workspace_id=tenant.workspace.id,
        organization_id=tenant.organization.id,
        user_id=tenant.contributor.user.id,
    )
    db_session.add(notification)
    db_session.flush()
    delivery = NotificationDelivery(
        notification_id=notification.id,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        channel=NotificationChannel.EMAIL,
        status=NotificationDeliveryStatus.FAILED,
        attempts=2,
        max_attempts=6,
        next_attempt_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        payload={"title": "t", "body": "b"},
    )
    db_session.add(delivery)
    db_session.commit()

    result = handle_pipeline_sweep_stuck({})

    assert result["notification_deliveries_requeued"] == 1
    db_session.expire_all()
    jobs = db_session.execute(
        select(Job).where(Job.job_type == outbox_dispatcher.JOB_TYPE)
    ).scalars().all()
    assert [job.payload.get("delivery_id") for job in jobs] == [str(delivery.id)]
