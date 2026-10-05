"""Obligations, live: a contract's dates become a calendar that raises its own alerts.

A services agreement (uploaded as a real PDF, read through the pipeline)
commences 1 January 2026 for twelve months, renews automatically unless
notice is given sixty days before the end of the term, and is paid on the
5th of each month. The engine must derive, deterministically:

  RENEWAL  2027-01-01
  NOTICE   2026-10-30   (the term ends 2026-12-31; 60 days before is Sunday
                         2026-11-01, and a "no later than" deadline on a
                         weekend rolls to the business day BEFORE it)
  PAYMENT  monthly on the 5th

Then: the hourly sweep moves the notice to DUE_SOON and OVERDUE on the right
days and emits the automation trigger once per state; the .ics / .csv exports
and a personal calendar feed (public token URL) carry the events; a VIEWER may
read and export but not create; a manual obligation can be completed.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from app.models.outbox_event import OutboxEvent
from app.services.obligations import service as obligation_service
from tests.conftest import TestSessionLocal
from tests.engines.conftest import Engines, drain

CONTRACT = [
    "MASTER SERVICES AGREEMENT CTR-OBL-1",
    "between Contoso Retail Private Limited and Acme Supplies Ltd",
    "1. Term. This Agreement commences on 1 January 2026 and continues for a period of twelve (12) months.",
    "2. Renewal. This Agreement shall automatically renew for successive periods of twelve (12) months",
    "unless either party gives written notice of non-renewal at least sixty (60) days before the end of",
    "the then-current term.",
    "3. Payment. The Customer shall pay the monthly fee on or before the 5th day of each month.",
    "4. Governing law. This Agreement is governed by the laws of India.",
]


@pytest.fixture()
def contract(engines: Engines) -> list[dict]:
    engines.process("msa.pdf", [CONTRACT], marker="CTR-OBL-1", classification="Contract",
                    entities={"party_names": ["Contoso Retail Private Limited", "Acme Supplies Ltd"],
                              "agreement_date": "2026-01-01", "governing_law": "India"})
    rows = engines.get("/obligations", params={"limit": 100}).json()["items"]
    assert rows, "no obligations extracted"
    return rows


def _by_kind(rows: list[dict], kind: str) -> list[dict]:
    return [r for r in rows if r["kind"] == kind]


def test_a_contracts_dates_are_derived(engines: Engines, contract: list[dict]) -> None:
    renewal = _by_kind(contract, "RENEWAL")
    notice = _by_kind(contract, "NOTICE")
    payment = _by_kind(contract, "PAYMENT")
    assert renewal and renewal[0]["due_date"] == "2027-01-01", [(r["kind"], r["due_date"]) for r in contract]
    assert notice and notice[0]["due_date"] == "2026-10-30", [(r["kind"], r["due_date"]) for r in contract]
    assert payment and payment[0]["recurrence"], [(r["kind"], r["due_date"], r["recurrence"]) for r in contract]
    assert date.fromisoformat(payment[0]["due_date"]).day == 5


def _sweep(at: datetime) -> dict:
    with TestSessionLocal() as db:
        report = obligation_service.sweep(db, at=at)
        db.commit()
    return report


def test_the_sweep_raises_due_soon_then_overdue_once(engines: Engines, contract: list[dict]) -> None:
    notice = _by_kind(contract, "NOTICE")[0]
    _sweep(datetime(2026, 10, 25, 12, tzinfo=timezone.utc))  # 5 days before; lead time is 14
    assert engines.get(f"/obligations/{notice['id']}").json()["obligation"]["state"] == "DUE_SOON"
    _sweep(datetime(2026, 11, 3, 12, tzinfo=timezone.utc))
    _sweep(datetime(2026, 11, 3, 13, tzinfo=timezone.utc))  # hourly: a second pass changes nothing
    assert engines.get(f"/obligations/{notice['id']}").json()["obligation"]["state"] == "OVERDUE"

    engines.refresh()
    events = [e.event_type for e in engines.db.execute(select(OutboxEvent).where(
        OutboxEvent.resource_id == notice["id"])).scalars()]
    assert events.count("trigger.obligation.due_soon") == 1, events
    assert events.count("trigger.obligation.overdue") == 1, events


def test_exports_and_a_personal_calendar_feed(engines: Engines, contract: list[dict]) -> None:
    ics = engines.get("/obligations/export", params={"format": "ics"}, as_user=engines.tenant.viewer)
    assert ics.status_code == 200, ics.text
    body = ics.text
    assert body.startswith("BEGIN:VCALENDAR") and body.count("BEGIN:VEVENT") >= 3, body[:500]
    assert "20261030" in body and "20270101" in body

    csv_export = engines.get("/obligations/export", params={"format": "csv"})
    assert csv_export.status_code == 200 and "2026-10-30" in csv_export.text

    issued = engines.post("/calendar-feeds", {"label": "My deadlines", "scope": "ALL"})
    assert issued.status_code in (200, 201), issued.text
    token = issued.json()["token"]
    public = engines.client.get(f"/api/v1/public/calendar-feeds/{token}.ics")
    assert public.status_code == 200, public.text
    assert "BEGIN:VCALENDAR" in public.text and "20261030" in public.text
    assert engines.client.get("/api/v1/public/calendar-feeds/not-a-real-token.ics").status_code == 404

    revoked = engines.delete(f"/calendar-feeds/{issued.json()['feed']['id']}")
    assert revoked.status_code in (200, 204)
    assert engines.client.get(f"/api/v1/public/calendar-feeds/{token}.ics").status_code in (404, 410)


def test_manual_obligations_and_roles(engines: Engines) -> None:
    body = {"kind": "PAYMENT", "title": "Pay the insurance premium", "rule": {"kind": "FIXED", "date": "2026-12-15"}}
    assert engines.post("/obligations", body, as_user=engines.tenant.viewer).status_code == 403
    created = engines.post("/obligations", body, as_user=engines.tenant.contributor)
    assert created.status_code in (200, 201), created.text
    ob = created.json()["obligation"] if "obligation" in created.json() else created.json()
    assert ob["due_date"] == "2026-12-15"
    done = engines.post(f"/obligations/{ob['id']}/complete", {"note": "Paid by wire"}, as_user=engines.tenant.contributor)
    assert done.status_code == 200, done.text
    assert done.json()["obligation"]["state"] == "DONE"
