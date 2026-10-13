"""Late usage for an hour that had no usage before is disclosed as late (F-256).

An hour's usage is closed once the seal pass has gone past it: invoices and the usage screens read
the sealed periods. Usage that arrives for a closed hour afterwards is rerouted into the current
hour and counted as *late*, so a customer sees why this month's figure moved. That worked only for
an hour whose rollup window already existed: sealing closed the windows it found, and an hour in
which nothing happened had no window, so an event that arrived later for it folded into a brand-new
(open) window as if it had been on time. The usage API test that sends its late event one minute
after the first one failed whenever it ran at minute 59, when that minute is the next hour.

These tests fix the clock instead of depending on the minute the suite runs at.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import rollup_service
from tests.services.test_arch14_gate_14_2_rollups import emit, org  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("test_database")

GRACE = 26


def _late(db: Session, org_id: uuid.UUID) -> tuple[Decimal, int]:
    row = db.execute(
        text(
            "SELECT COALESCE(sum(late_quantity), 0), COALESCE(sum(late_cost_micros), 0) "
            "FROM usage_rollups WHERE organization_id = :org AND granularity = 'HOUR' "
            "AND event_type = 'llm.input_token' "
            "AND grain = 'ORG_TOTAL'"
        ),
        {"org": org_id},
    ).one()
    return Decimal(row[0]), int(row[1])


@pytest.mark.parametrize("minute", [0, 30, 59])
def test_late_usage_into_an_hour_with_no_earlier_usage_is_disclosed(
    db_session: Session, org: uuid.UUID, minute: int  # noqa: F811
) -> None:
    now = datetime(2026, 6, 20, 10, minute, 30, tzinfo=timezone.utc)
    old = now - timedelta(hours=40)

    emit(db_session, org=org, occurred_at=old, quantity=10, cost=10)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=now)
    rollup_service.seal_due(db_session, now=now, grace_hours=GRACE)
    db_session.flush()

    # One minute after the first event: the same hour, or (at minute 59) the next one, which had
    # no usage when the seal pass went past it.
    emit(db_session, org=org, occurred_at=old + timedelta(minutes=1), quantity=7, cost=70)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=now)
    db_session.flush()

    late_quantity, late_cost = _late(db_session, org)
    assert late_quantity == Decimal(7), f"at minute {minute} the late usage was disclosed as {late_quantity}"
    assert late_cost == 70


def test_a_backlog_that_was_never_rolled_up_is_not_called_late(
    db_session: Session, org: uuid.UUID  # noqa: F811
) -> None:
    """Usage recorded on time but rolled up late (the rollup worker was down) is not late usage.

    The seal pass refuses to go past unaggregated usage, so an hour with a backlog stays open and
    the backlog lands in its own hour when the rollup catches up.
    """
    now = datetime(2026, 6, 20, 10, 15, 0, tzinfo=timezone.utc)
    on_time = now - timedelta(hours=40)
    emit(db_session, org=org, occurred_at=on_time - timedelta(hours=2), quantity=5, cost=5)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=now)
    # Recorded, not yet rolled up, when the seal pass runs.
    emit(db_session, org=org, occurred_at=on_time, quantity=3, cost=30)
    db_session.flush()
    rollup_service.seal_due(db_session, now=now, grace_hours=GRACE)
    rollup_service.run_rollup(db_session, now=now)
    db_session.flush()

    assert _late(db_session, org) == (Decimal(0), 0)
