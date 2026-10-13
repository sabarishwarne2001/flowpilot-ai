"""A document the verification agents agree on runs its workflows once (campaign session 3).

With multi-agent verification on, the workflow engine holds a document's rules while its
verification is pending, so nobody acts on fields that are still being checked; a person's review
releases them, and so does the agents' own agreement. This pins the agreed path: a "Document
processed" rule runs for the document exactly once (not never, and not again on the release).

HANDOFF (session 4): whether a rule can be skipped for good when the engine job runs while the
verify job is mid-flight (its PENDING record is committed before the agents are called, and the
agents' release event is written without an engine job) is recorded there as unverified.
"""

from __future__ import annotations

from tests.engines.conftest import Engines

TEXT = ["ACME SUPPLIES LTD", "TAX INVOICE", "Invoice No: INV-VR-1", "Total: 120.00 INR"]
TRUTH = {"vendor_name": "Acme Supplies Ltd", "invoice_number": "INV-VR-1", "total_amount": "120.00",
         "currency": "INR"}

RULE = {
    "name": "Tag verified invoices",
    "triggers": ["document.ready"],
    "condition_groups": [],
    "groups_operator": "AND",
    "actions": [
        {"action_type": "work_item.mutate", "config": {"target_field": "summary",
                                                       "target_value": "Checked and filed"}},
    ],
}


def _runs(engines: Engines, rule_id: str) -> list[dict]:
    page = engines.get("/automation/executions", params={"rule_id": rule_id}).json()
    items = page if isinstance(page, list) else page.get("items", [])
    return [run for run in items if str(run.get("rule_id")) == rule_id]


def test_an_auto_approved_document_runs_its_document_ready_rules(engines: Engines) -> None:
    engines.plan("business")
    assert engines.put("/document-settings/", {"verification_enabled": True, "verification_agents": 2}).status_code == 200
    rule = engines.post("/automation/rules", RULE)
    assert rule.status_code == 201, rule.text

    item = engines.process("verified.pdf", [TEXT], marker="INV-VR-1", classification="Invoice", entities=TRUTH,
                           agents=[TRUTH, TRUTH])
    engines.refresh()
    from sqlalchemy import select
    from app.models.verification import DocumentVerification

    verification = engines.db.execute(
        select(DocumentVerification).where(DocumentVerification.work_item_id == item)).scalar_one()
    assert verification.status.value in ("AGREED", "AUTO_APPROVED"), (verification.status, verification.details)

    runs = _runs(engines, rule.json()["id"])
    assert [str(run["work_item_id"]) for run in runs] == [str(item)], runs
    assert engines.item(item).summary == "Checked and filed"
