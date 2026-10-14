"""Every field of the exception agent's policy changes what the agent does (campaign session 4).

    pytest tests/engines/test_process_agent_policy_takes_effect_live.py -q

`Process intelligence > Agent` stores four settings: planning on or off, automatic application on
or off, the kinds of proposal that may apply themselves, and the undo hold in minutes. Each had a
reader and no test. Here an admin saves them through the endpoint the page calls and the agent runs
through the sweep endpoint, on a real extraction review item: an invoice read by agreeing
verification agents that calibrated autonomy held for a person because the organization had no
fitted model yet. A model is then fitted (reviewed history, error limit saved through the autonomy
endpoint, as in test_autonomy_limit_changes_decisions_live.py), so the only thing between the
agent and an automatic resolution is the policy.

Two things are pinned: the process clock (so "after the hold" is a known moment) and the 1 % audit
draw (keyed on a random proposal id, so it would otherwise send one run in a hundred to review).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.verification import DocumentVerification
from app.services.process_intel import service
from tests.engines.conftest import Engines
from tests.engines.test_autonomy_limit_changes_decisions_live import _reviewed_history, _save_limit

KIND = "extraction.approve_consensus"
ENTITIES = {"vendor_name": "Acme Supplies Ltd", "total_amount": "99.00", "currency": "USD"}


class _Clock:
    def __init__(self) -> None:
        self.at = datetime.now(timezone.utc).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture()
def held(engines: Engines, monkeypatch: pytest.MonkeyPatch):
    """An open extraction review item held by calibration alone; then a model that would let it through."""
    from app.services.calibration import sampling

    clock = _Clock()
    monkeypatch.setattr(service, "now", clock)
    monkeypatch.setattr(sampling, "is_audit_sample", lambda *args, **kwargs: False)

    assert engines.put("/document-settings/", {"verification_enabled": True}).status_code == 200
    number = f"INV-AGT-{uuid.uuid4().hex[:6].upper()}"
    work_item_id = engines.process(f"{number}.pdf", [["ACME SUPPLIES LTD", "TAX INVOICE",
                                                      f"Invoice Number: {number}", "Total: 99.00 USD"]],
                                   marker=number, classification="Invoice",
                                   entities={**ENTITIES, "invoice_number": number})
    engines.refresh()
    verification = _verification(engines, work_item_id)
    assert verification.status.value == "DISAGREED", verification.status
    assert verification.details["calibration"]["review_all_fields"] is True

    _reviewed_history(engines)
    assert _save_limit(engines, "0.15")["threshold"] is not None
    return engines, clock, verification.id, work_item_id


def _verification(engines: Engines, work_item_id: uuid.UUID) -> DocumentVerification:
    engines.refresh()
    return engines.db.execute(
        select(DocumentVerification).where(DocumentVerification.work_item_id == work_item_id)).scalar_one()


def _policy(engines: Engines, *, planning: bool = True, auto: bool = False, kinds: tuple[str, ...] = (),
            hold: int = 30) -> dict:
    body = {"planning_enabled": planning, "auto_apply_enabled": auto, "auto_apply_kinds": list(kinds),
            "hold_minutes": hold}
    saved = engines.put("/process/agent/policy", body)
    assert saved.status_code == 200, saved.text
    for key, value in body.items():
        assert saved.json()[key] == value, (key, saved.json())
    return saved.json()


def _sweep(engines: Engines) -> dict:
    response = engines.post("/process/sweep")
    assert response.status_code == 200, response.text
    return response.json()["report"]["agent"]


def _proposals(engines: Engines, subject_id: uuid.UUID) -> list[dict]:
    response = engines.get("/process/agent/proposals", params={"subject_id": str(subject_id)})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_planning_off_proposes_nothing_and_auto_apply_off_only_proposes(held) -> None:
    engines, _, item_id, _ = held
    _policy(engines, planning=False)
    report = _sweep(engines)
    assert report["plan"] == {"planned": 0, "skipped": 0}, report
    assert _proposals(engines, item_id) == [], "planning is off: the agent proposed anyway"

    _policy(engines, planning=True, auto=False)
    _sweep(engines)
    [proposal] = _proposals(engines, item_id)
    assert proposal["proposal_kind"] == KIND
    assert proposal["status"] == "PROPOSED" and proposal["apply_after"] is None
    assert proposal["holds"] == ["POLICY_OFF"], "only the policy should hold this proposal back"


def test_a_kind_not_switched_on_is_only_proposed(held) -> None:
    engines, _, item_id, _ = held
    _policy(engines, auto=True, kinds=("assertion.accept_engine",))
    _sweep(engines)
    [proposal] = _proposals(engines, item_id)
    assert proposal["status"] == "PROPOSED", proposal
    assert proposal["holds"] == ["KIND_OFF"], proposal


def test_an_automatic_resolution_waits_the_hold_then_applies(held) -> None:
    engines, clock, item_id, work_item_id = held
    _policy(engines, auto=True, kinds=(KIND,), hold=45)
    scheduled_at = clock.at
    _sweep(engines)
    [proposal] = _proposals(engines, item_id)
    assert proposal["status"] == "AUTO_SCHEDULED", proposal
    assert proposal["holds"] == [] and proposal["auto"] is True
    assert datetime.fromisoformat(proposal["apply_after"]) == scheduled_at + timedelta(minutes=45)

    clock.at = scheduled_at + timedelta(minutes=44)
    assert _sweep(engines)["auto"]["applied"] == 0
    assert _proposals(engines, item_id)[0]["status"] == "AUTO_SCHEDULED", "applied before its hold ran out"
    assert _verification(engines, work_item_id).status.value == "DISAGREED"

    clock.at = scheduled_at + timedelta(minutes=46)
    assert _sweep(engines)["auto"]["applied"] == 1
    [applied] = _proposals(engines, item_id)
    assert applied["status"] in ("APPLIED", "AUTO_APPLIED"), applied
    assert _verification(engines, work_item_id).status.value == "REVIEWED"


def test_switching_auto_apply_off_stops_a_scheduled_resolution(held) -> None:
    engines, clock, item_id, work_item_id = held
    _policy(engines, auto=True, kinds=(KIND,), hold=30)
    scheduled_at = clock.at
    _sweep(engines)
    assert _proposals(engines, item_id)[0]["status"] == "AUTO_SCHEDULED"

    _policy(engines, auto=False)
    clock.at = scheduled_at + timedelta(minutes=31)
    report = _sweep(engines)
    assert report["auto"]["applied"] == 0 and report["auto"]["held"] == 1, report
    [proposal] = _proposals(engines, item_id)
    assert proposal["status"] == "PROPOSED" and "POLICY_OFF" in proposal["holds"], proposal
    assert _verification(engines, work_item_id).status.value == "DISAGREED", "applied after auto-apply was switched off"
