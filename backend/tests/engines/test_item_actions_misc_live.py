"""Smaller item actions work live (campaign session 2, ledger).

    pytest tests/engines/test_item_actions_misc_live.py -q

These routes had only their refusals proven: one anomaly finding read and confirmed, one
automation run read, the calibrated-autonomy reliability curve and resume, a legal hold placed
and released, a resumable upload abandoned, and the public API's single document and workflow
list.
"""

from __future__ import annotations

from tests.engines.conftest import Engines, drain, make_pdf
from tests.engines.test_automation_live import RULE, _invoice as _automation_invoice
from tests.engines.test_radar_live import _findings, _invoice as _radar_invoice, _process


def test_an_anomaly_is_read_and_confirmed(engines: Engines) -> None:
    pdf = make_pdf([_radar_invoice("INV-IA-300", "500.00", "2026-08-01")])
    _process(engines, "INV-IA-300", "500.00", "2026-08-01", data=pdf)
    _process(engines, "INV-IA-300", "500.00", "2026-08-01", filename="dup.pdf", data=pdf)
    finding = next(f for f in _findings(engines) if f["layer"] == "L0")
    detail = engines.get(f"/anomalies/{finding['id']}")
    assert detail.status_code == 200, detail.text
    confirmed = engines.post(f"/anomalies/{finding['id']}/confirm", {"note": "Paid twice by mistake; recovering."},
                             as_user=engines.tenant.contributor)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "CONFIRMED"


def test_one_automation_run_is_read(engines: Engines) -> None:
    _automation_invoice(engines, "INV-IA-AU-0", "10.00")
    rule = engines.post("/automation/rules", RULE).json()
    _automation_invoice(engines, "INV-IA-AU-1", "5000.00")
    runs = engines.get("/automation/executions", params={"rule_id": rule["id"]}).json()
    items = runs if isinstance(runs, list) else runs.get("items", [])
    assert items, runs
    one = engines.get(f"/automation/executions/{items[0]['id']}")
    assert one.status_code == 200, one.text
    assert str(one.json()["rule_id"]) == rule["id"]


def test_autonomy_reliability_and_resume(engines: Engines) -> None:
    engines.plan("enterprise")
    org = engines.tenant.organization.id
    base = f"/api/v1/organizations/{org}/autonomy/verification.document"
    reliability = engines.client.get(f"{base}/reliability", headers=engines.tenant.owner.headers)
    assert reliability.status_code == 200, reliability.text
    resumed = engines.client.post(f"{base}/resume", headers=engines.tenant.owner.headers)
    # Nothing is suspended on a new organization: resuming says so instead of failing.
    assert resumed.status_code in (200, 409), resumed.text


def test_a_legal_hold_is_placed_and_released(engines: Engines) -> None:
    work_item_id = engines.upload("held.pdf", make_pdf([["Board minutes", "Privileged"]]))
    drain()
    placed = engines.post("/work-items/retention-holds", {"reason": "Litigation hold LIT-42",
                                                          "work_item_id": str(work_item_id)})
    assert placed.status_code in (200, 201), placed.text
    refused = engines.delete(f"/work-items/{work_item_id}")
    assert refused.status_code == 409, refused.text
    released = engines.delete(f"/work-items/retention-holds/{placed.json()['id']}")
    assert released.status_code == 200, released.text
    assert engines.delete(f"/work-items/{work_item_id}").status_code == 204


def test_an_abandoned_resumable_upload_is_cancelled(engines: Engines) -> None:
    session = engines.post("/upload-sessions", {"filename": "big.pdf", "mime_type": "application/pdf",
                                                "total_size": 10_000_000})
    assert session.status_code in (200, 201), session.text
    session_id = session.json()["id"]
    assert engines.delete(f"/upload-sessions/{session_id}").status_code == 204
    after = engines.get(f"/upload-sessions/{session_id}")
    assert after.status_code == 404 or after.json().get("status") in ("ABORTED", "CANCELLED"), after.text


def test_the_public_api_reads_one_document_and_lists_workflows(engines: Engines) -> None:
    t = engines.tenant
    issued = engines.post("/developer/keys", {"name": "ERP", "scopes": ["public_documents:read", "public_workflows:read"],
                                              "tier_key": "FREE", "enable_public_api": True}, org=True, as_user=t.org_admin)
    assert issued.status_code == 201, issued.text
    headers = {"Authorization": f"Bearer {issued.json()['token']}"}
    work_item_id = engines.process("pub.pdf", [["TAX INVOICE", "Invoice No: INV-IA-PUB"]], marker="INV-IA-PUB",
                                   classification="Invoice", entities={"invoice_number": "INV-IA-PUB"})
    one = engines.client.get(f"/api/v1/public/documents/{work_item_id}", params={"workspace_id": str(engines.ws)},
                             headers=headers)
    assert one.status_code == 200, one.text
    workflows = engines.client.get("/api/v1/public/workflows", params={"workspace_id": str(engines.ws)}, headers=headers)
    assert workflows.status_code == 200, workflows.text
