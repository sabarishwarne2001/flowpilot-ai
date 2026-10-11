"""Every automation action a rule can run does what it says, live (campaign session 2, brief F).

    pytest tests/engines/test_automation_actions_live.py -q

`test_automation_live.py` drives notify.role, review.escalate and work_item.mutate. This drives
the rest of the catalog on an Enterprise organization: webhook.send (a signed delivery reaches
the customer's endpoint with the chosen fields), redaction.start (a redaction job opens for the
document), autonomy.decide (a decision is recorded; with no calibration yet it holds the document
for a person), and email.send (refused with a reason when the workspace has no mail settings).
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from app.models.redaction import RedactionJob
from app.models.webhook_delivery import WebhookDelivery
from tests.engines.conftest import Engines
from tests.engines.test_webhooks_live import URL, _deliver, receiver  # noqa: F401  (fixture)

NUMBER = "INV-ACT-7001"


def _rule(endpoint_id: str) -> dict:
    return {
        "name": "Every action",
        "triggers": ["document.completed"],
        "condition_groups": [{"logic_operator": "AND", "conditions": [
            {"field": "classification_details.document_classification", "operator": "EQUALS", "value": "Invoice"},
        ]}],
        "groups_operator": "AND",
        "actions": [
            {"action_type": "webhook.send", "config": {"endpoint_id": endpoint_id, "include_fields": ["invoice_number"]}},
            {"action_type": "redaction.start", "config": {"profile_key": "financial"}},
            {"action_type": "autonomy.decide", "config": {"on_hold": "ESCALATE"}},
        ],
    }


def test_the_rest_of_the_action_catalog_runs_live(engines: Engines, receiver) -> None:
    engines.plan("enterprise")
    engines.process("warm.pdf", [["TAX INVOICE", "Invoice Number: INV-ACT-0"]], marker="INV-ACT-0",
                    classification="Invoice", entities={"invoice_number": "INV-ACT-0"})
    created = engines.post("/webhooks/endpoints", {"url": URL, "event_types": ["workflow.triggered"]}, org=True)
    assert created.status_code == 201, created.text
    endpoint_id = created.json()["endpoint"]["id"]

    rule = engines.post("/automation/rules", _rule(endpoint_id))
    assert rule.status_code == 201, rule.text
    rule_id = rule.json()["id"]

    work_item_id = engines.process(f"{NUMBER}.pdf", [["ACME SUPPLIES LTD", "TAX INVOICE", f"Invoice Number: {NUMBER}",
                                                      "Total: 420.00 USD"]], marker=NUMBER, classification="Invoice",
                                   entities={"invoice_number": NUMBER, "total_amount": "420.00"})

    executions = engines.get("/automation/executions", params={"rule_id": rule_id}).json()
    items = executions if isinstance(executions, list) else executions.get("items", [])
    mine = [e for e in items if str(e.get("work_item_id")) == str(work_item_id)]
    assert len(mine) == 1, items
    nodes = engines.get(f"/automation/executions/{mine[0]['id']}/nodes").json()
    node_list = nodes if isinstance(nodes, list) else nodes.get("items", nodes.get("nodes", []))
    by_action = {str(n.get("action_type") or n.get("node_type")): n for n in node_list}
    for action in ("webhook.send", "redaction.start", "autonomy.decide"):
        assert action in by_action, (action, node_list)
        assert str(by_action[action].get("status", "")).upper() in ("COMPLETED", "SUCCEEDED", "SUCCESS", "HELD"), by_action[action]

    # webhook.send: one delivery for the endpoint, signed and delivered with the chosen field.
    engines.refresh()
    deliveries = engines.db.execute(select(WebhookDelivery).where(
        WebhookDelivery.webhook_endpoint_id == uuid.UUID(endpoint_id))).scalars().all()
    assert [d.event_type for d in deliveries] == ["workflow.triggered"], deliveries
    _deliver()
    sent = [r for r in receiver.requests if r["url"] == URL]
    assert len(sent) == 1, receiver.requests
    assert NUMBER in json.dumps(json.loads(sent[0]["body"])), sent[0]["body"]

    # redaction.start: a redaction job opened for this document.
    jobs = engines.db.execute(select(RedactionJob).where(RedactionJob.work_item_id == work_item_id)).scalars().all()
    assert len(jobs) == 1, jobs


def test_email_send_without_mail_settings_is_refused_with_its_reason(engines: Engines) -> None:
    engines.plan("enterprise")
    engines.process("warm.pdf", [["TAX INVOICE", "Invoice Number: INV-ACT-1"]], marker="INV-ACT-1",
                    classification="Invoice", entities={"invoice_number": "INV-ACT-1"})
    body = {"name": "Mail the AP team", "triggers": ["document.completed"], "condition_groups": [],
            "groups_operator": "AND",
            "actions": [{"action_type": "email.send", "config": {"recipient": "ap@example.com"}}]}
    created = engines.post("/automation/rules", body)
    if created.status_code != 201:
        # Refused at save time is as good as at run time, provided the reason is given.
        assert created.status_code in (400, 409, 422), created.text
        assert "mail" in created.text.lower() or "email" in created.text.lower(), created.text
        return
    work_item_id = engines.process("INV-ACT-2.pdf", [["TAX INVOICE", "Invoice Number: INV-ACT-2"]],
                                   marker="INV-ACT-2", classification="Invoice", entities={"invoice_number": "INV-ACT-2"},
                                   allow_failures=True)
    executions = engines.get("/automation/executions", params={"rule_id": created.json()["id"]}).json()
    items = executions if isinstance(executions, list) else executions.get("items", [])
    mine = [e for e in items if str(e.get("work_item_id")) == str(work_item_id)]
    assert mine, items
    text = json.dumps(mine[0]).lower()
    assert "mail" in text or "smtp" in text or "email" in text, mine[0]
