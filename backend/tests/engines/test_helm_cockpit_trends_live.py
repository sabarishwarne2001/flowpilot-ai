"""Each cockpit instrument's small trend: one point a day, the day's last plan, oldest first, for
the last 30 days; a mandate's figure is never invented for a day it had no plan."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models.helm import HelmBook, HelmPlan
from app.services.helm import service
from app.services.helm import vocabulary as v

MANDATE = str(uuid.uuid4())


def _plan(db_session, book, version, at, figure, state="holding"):
    db_session.add(HelmPlan(organization_id=book.organization_id, book_id=book.id, version=version,
                            status=v.PLAN_SUPERSEDED if version < 9 else v.PLAN_CURRENT, inputs_digest="a" * 64,
                            seed=1, engine_version="test", horizon_days=30, solver_status="OPTIMAL",
                            triggered_by="SCHEDULE", created_at=at,
                            summary={"instruments": [{"mandate_id": MANDATE, "state": state, "figure_value": figure}],
                                     "simulation": {"paths": list(range(1000))}}))


def test_one_point_a_day_from_the_days_last_plan(db_session, tenant) -> None:
    book = HelmBook(organization_id=tenant.organization.id, workspace_id=tenant.workspace.id, kind=v.BOOK_WORKSPACE,
                    name="Engineering", status=v.BOOK_ACTIVE)
    db_session.add(book)
    db_session.flush()
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    _plan(db_session, book, 1, now - timedelta(days=40), 1.0)          # older than 30 days: left out
    _plan(db_session, book, 2, now - timedelta(days=2, hours=3), 5.0)  # superseded the same day
    _plan(db_session, book, 3, now - timedelta(days=2), 7.0, "drifting")
    _plan(db_session, book, 4, now - timedelta(days=1), 3.0)
    _plan(db_session, book, 9, now, 2.0)
    db_session.flush()

    points = service.instrument_trends(db_session, book)[MANDATE]
    assert [p["value"] for p in points] == [7.0, 3.0, 2.0]
    assert points[0]["state"] == "drifting"
    assert [p["on"] for p in points] == sorted(p["on"] for p in points)
