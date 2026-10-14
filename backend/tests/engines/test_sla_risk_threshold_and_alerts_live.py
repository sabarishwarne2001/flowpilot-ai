"""The SLA at-risk probability and the alert switch change what the sweep flags and raises (campaign session 4).

    pytest tests/engines/test_sla_risk_threshold_and_alerts_live.py -q

`Process intelligence > SLA` stores, per object type, a target, the probability from which an open
item is flagged AT RISK, and whether that raises an alert (the `trigger.process.sla_at_risk` event
workflows listen to). Only the target was ever exercised. Here an admin saves each setting through
the endpoint the page calls and runs the sweep through its own endpoint; the predictions are read
back from the SLA endpoint. The one stand-in is the review items' history: instead of months of
event log, `sla.load_instances` returns 150 finished review items (half of one kind, which breaches
the 24-hour target four times in five; half of another, which always meets it) and two open ones,
one of each kind. The model, the Brier check, the predictions and the alerts are the product's.

* At 0.5 the open item of the breaching kind is AT RISK, the other OK; with alerts off nothing is raised.
* A threshold just above that item's probability leaves it OK; one at or just below it flags it again.
* With alerts on, it raises exactly one alert, once: the next sweep does not raise it again.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_FLOOR, Decimal

import pytest
from sqlalchemy import select

from app.models.outbox_event import OutboxEvent
from app.services.process_intel import service, sla
from app.services.process_intel import vocabulary as v
from tests.engines.conftest import Engines

NOW = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
NAMESPACE = uuid.UUID("5a1a0000-0000-4000-8000-000000000365")
SLOW_OPEN = str(uuid.uuid5(NAMESPACE, "open-slow"))
FAST_OPEN = str(uuid.uuid5(NAMESPACE, "open-fast"))


def _history() -> list[sla.Instance]:
    out = []
    for n in range(150):
        start = NOW - timedelta(days=30) + timedelta(hours=3 * n)
        slow = n % 2 == 0
        breached = slow and n % 10 != 0
        end = start + timedelta(hours=30 if breached else 10)
        out.append(sla.Instance(object_id=str(uuid.uuid5(NAMESPACE, f"done-{n}")), kind="slow" if slow else "fast",
                                start=start, end=end,
                                events=[(start, "review.opened", v.ACTOR_SYSTEM),
                                        (end, "review.closed", v.ACTOR_PERSON)]))
    for object_id, kind in ((SLOW_OPEN, "slow"), (FAST_OPEN, "fast")):
        start = NOW - timedelta(hours=3)
        out.append(sla.Instance(object_id=object_id, kind=kind, start=start, end=None,
                                events=[(start, "review.opened", v.ACTOR_SYSTEM)]))
    return out


@pytest.fixture()
def history(engines: Engines, monkeypatch: pytest.MonkeyPatch) -> Engines:
    monkeypatch.setattr(service, "now", lambda: NOW)

    def load(db, *, workspace_id, object_type, now):
        return _history() if object_type == v.OBJECT_REVIEW_ITEM else []

    monkeypatch.setattr(sla, "load_instances", load)
    return engines


def _policy(engines: Engines, probability: Decimal | float, *, alerts: bool) -> None:
    body = {"target_hours": 24, "at_risk_probability": float(probability), "alerts_enabled": alerts}
    saved = engines.put("/process/sla/REVIEW_ITEM", body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["at_risk_probability"] == float(probability)
    assert saved.json()["alerts_enabled"] is alerts


def _sweep(engines: Engines) -> dict:
    response = engines.post("/process/sweep")
    assert response.status_code == 200, response.text
    run = response.json()["report"]["sla"]["REVIEW_ITEM"]
    assert run["status"] == v.RUN_ACCEPTED, run
    return run


def _predictions(engines: Engines) -> dict[str, dict]:
    body = engines.get("/process/sla").json()
    return {p["object_id"]: p for p in body["predictions"] if p["object_type"] == "REVIEW_ITEM"}


def _alerts(engines: Engines) -> list[OutboxEvent]:
    engines.refresh()
    return list(engines.db.execute(select(OutboxEvent).where(
        OutboxEvent.workspace_id == engines.ws, OutboxEvent.event_type == v.EVENT_SLA_AT_RISK)).scalars())


def test_the_threshold_decides_at_risk_and_the_switch_decides_the_alert(history: Engines) -> None:
    engines = history
    _policy(engines, 0.5, alerts=False)
    run = _sweep(engines)
    shown = _predictions(engines)
    slow, fast = shown[SLOW_OPEN], shown[FAST_OPEN]
    assert slow["state"] == "AT_RISK" and fast["state"] == "OK", (slow, fast)
    assert run["alerts"] == 0 and _alerts(engines) == [], "alerts are off: nothing may be raised"
    assert slow["alerted_at"] is None

    # The item's probability as shown (five places): a threshold just above it, and one at or just below it.
    probability = Decimal(str(slow["probability"]))
    above = probability.quantize(Decimal("0.001")) + Decimal("0.001")
    at_or_below = (probability - Decimal("0.00001")).quantize(Decimal("0.001"), rounding=ROUND_FLOOR)
    assert Decimal("0.5") < above < 1, f"the breaching kind should be likely, not certain: {probability}"
    assert Decimal(str(fast["probability"])) < at_or_below, (fast, at_or_below)

    _policy(engines, above, alerts=False)
    _sweep(engines)
    assert _predictions(engines)[SLOW_OPEN]["state"] == "OK", f"{probability} is below the {above} threshold"

    _policy(engines, at_or_below, alerts=True)
    run = _sweep(engines)
    shown = _predictions(engines)
    assert shown[SLOW_OPEN]["state"] == "AT_RISK" and shown[FAST_OPEN]["state"] == "OK", shown
    assert run["alerts"] == 1, run
    [alert] = _alerts(engines)
    assert str(alert.resource_id) == SLOW_OPEN
    assert alert.payload["object_type"] == "REVIEW_ITEM" and alert.payload["target_hours"] == 24
    assert shown[SLOW_OPEN]["alerted_at"] is not None

    # Raised once: the item is still at risk on the next sweep, and nothing is raised again.
    run = _sweep(engines)
    assert run["alerts"] == 0, run
    assert len(_alerts(engines)) == 1
    assert _predictions(engines)[SLOW_OPEN]["state"] == "AT_RISK"
