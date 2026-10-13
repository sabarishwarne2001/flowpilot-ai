"""The server writes calendar dates in the workspace's date format (campaign session 3, N-064).

`Settings > Workspace > Date format` was read only by three-way matching. Now a calendar date the
server puts into a message people read (an automation's email or notification: "Rent is due on
{{event.due_date}}") reads in that format, as the dates on the workspace's documents do. Moments
(timestamps) are left as they are, and webhook payloads and exports keep ISO 8601.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.core.display_format import DATE_FORMATS, format_calendar_date, is_iso_date
from app.services.automation.actions.base import render, variables_for
from tests.conftest import Fixture


@pytest.mark.parametrize("fmt,expected", [
    ("YYYY-MM-DD", "2026-12-05"), ("DD-MM-YYYY", "05-12-2026"), ("MM-DD-YYYY", "12-05-2026"),
    ("DD/MM/YYYY", "05/12/2026"), ("MM/DD/YYYY", "12/05/2026"), ("YYYY/MM/DD", "2026/12/05"),
])
def test_every_offered_format(fmt: str, expected: str) -> None:
    assert fmt in DATE_FORMATS
    assert format_calendar_date(date(2026, 12, 5), fmt) == expected
    assert format_calendar_date("2026-12-05", fmt) == expected


def test_unknown_formats_and_non_dates_are_left_safe() -> None:
    assert format_calendar_date("2026-12-05", "DD.MM.YY") == "2026-12-05"
    assert format_calendar_date("next Tuesday", "DD/MM/YYYY") == "next Tuesday"
    assert format_calendar_date("2026-02-30", "DD/MM/YYYY") == "2026-02-30"
    assert format_calendar_date(datetime(2026, 12, 5, 23, 0, tzinfo=timezone.utc), "DD/MM/YYYY") == "05/12/2026"
    assert is_iso_date("2026-12-05") and not is_iso_date("2026-12-05T10:00:00Z")


def _state(db: Session, tenant: Fixture, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        db=db, execution=SimpleNamespace(workspace_id=tenant.workspace.id), work_item=None, rule=None,
        trigger_event=SimpleNamespace(event_type="obligation.due", payload=payload),
    )


def test_an_automation_message_reads_the_due_date_in_the_workspace_format(
    db_session: Session, tenant: Fixture
) -> None:
    tenant.workspace.date_format = "DD/MM/YYYY"
    db_session.commit()
    values = variables_for(_state(db_session, tenant, {
        "due_date": "2026-12-15", "title": "Rent", "occurred_at": "2026-12-10T09:30:00+00:00",
    }))
    assert render("{{event.title}} is due on {{event.due_date}}", values) == "Rent is due on 15/12/2026"
    assert values["event.occurred_at"] == "2026-12-10T09:30:00+00:00", "a moment is not a calendar date"


def test_the_default_format_is_iso(db_session: Session, tenant: Fixture) -> None:
    values = variables_for(_state(db_session, tenant, {"due_date": "2026-12-15"}))
    assert values["event.due_date"] == "2026-12-15"
