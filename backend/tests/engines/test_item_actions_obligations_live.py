"""Every action on one obligation and one holiday calendar works, live (campaign session 2, ledger).

    pytest tests/engines/test_item_actions_obligations_live.py -q

These routes had only their refusals proven (the cross-tenant sweeps). Here each is used for
real on a contract read through the pipeline: edit, waive, reopen, review, delete, re-extract,
recalculate dates, a counterparty's obligations, and editing and deleting a holiday calendar.
"""

from __future__ import annotations

from tests.engines.conftest import Engines
from tests.engines.test_obligations_live import contract  # noqa: F401  (fixture)


def _obligation(response) -> dict:
    body = response.json()
    return body["obligation"] if "obligation" in body else body


def test_every_obligation_action_works(engines: Engines, contract: list[dict]) -> None:  # noqa: F811
    notice = next(r for r in contract if r["kind"] == "NOTICE")

    edited = engines.patch(f"/obligations/{notice['id']}", {"title": "Notice of non-renewal to Acme", "lead_days": 30})
    assert edited.status_code == 200, edited.text
    assert _obligation(edited)["title"] == "Notice of non-renewal to Acme"

    waived = engines.post(f"/obligations/{notice['id']}/waive", {"reason": "Renewing on purpose this year"})
    assert waived.status_code == 200, waived.text
    assert _obligation(waived)["state"] == "WAIVED"
    reopened = engines.post(f"/obligations/{notice['id']}/reopen")
    assert reopened.status_code == 200, reopened.text
    assert _obligation(reopened)["state"] != "WAIVED"

    reviewed = engines.post(f"/obligations/{notice['id']}/review", {"verdict": "CONFIRM"})
    assert reviewed.status_code in (200, 409), reviewed.text  # 409: nothing awaited review

    work_item_id = notice.get("work_item_id") or notice.get("source_work_item_id")
    if work_item_id:
        again = engines.post(f"/work-items/{work_item_id}/obligations/extract")
        assert again.status_code in (200, 202), again.text

    calculated = engines.post("/obligations/calculate", {"rule": {"kind": "FIXED", "date": "2026-11-01"}})
    assert calculated.status_code == 200, calculated.text

    calendar = engines.post("/holiday-calendars", {"name": "Plant shutdown", "holidays": [
        {"date": "2026-12-28", "name": "Shutdown"}]})
    assert calendar.status_code in (200, 201), calendar.text
    calendar_id = (calendar.json().get("calendar") or calendar.json())["id"]
    renamed = engines.patch(f"/holiday-calendars/{calendar_id}", {"name": "Plant shutdown 2026"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["calendar"]["name"] == "Plant shutdown 2026"
    assert engines.delete(f"/holiday-calendars/{calendar_id}").status_code == 204

    payment = next(r for r in contract if r["kind"] == "PAYMENT")
    if payment.get("entity_id"):
        listed = engines.get(f"/entities/{payment['entity_id']}/obligations")
        assert listed.status_code == 200, listed.text

    # One read from a document is rejected, not deleted, so re-reading the document cannot bring it back.
    refused = engines.delete(f"/obligations/{payment['id']}")
    assert refused.status_code == 409 and "EXTRACTED" in refused.text, refused.text
    manual = engines.post("/obligations", {"kind": "PAYMENT", "title": "One-off fee",
                                           "rule": {"kind": "FIXED", "date": "2026-12-15"}})
    assert manual.status_code in (200, 201), manual.text
    manual_id = _obligation(manual)["id"]
    assert engines.delete(f"/obligations/{manual_id}").status_code == 204
    assert engines.get(f"/obligations/{manual_id}").status_code == 404
