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
