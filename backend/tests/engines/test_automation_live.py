"""Workflow builder and DAG runner, live.

A workspace admin builds, through the API exactly as the flow builder sends
it, the rule

    WHEN   a document's pipeline completes
    ONLY IF  it is an Invoice  AND  its total is greater than 1,000
    THEN   notify the organization admins, and send it to review
    OTHERWISE  set its summary to "Auto-cleared: below threshold"

Then real documents run through the pipeline:

* the rule is saved ENABLED and reads back as built;
* a 5,000 invoice runs the THEN branch: an in-app notification for the
  admins and an extraction review item in the hub;
* a 50 invoice runs the OTHERWISE branch (the summary is set);
* Run history lists both executions with their node-by-node traces;
* disabling the rule stops it running; a CONTRIBUTOR may not create rules.
"""

from __future__ import annotations

from tests.engines.conftest import Engines

RULE = {
    "name": "Large invoices to review",
    "triggers": ["document.completed"],
    "condition_groups": [{"logic_operator": "AND", "conditions": [
        {"field": "classification_details.document_classification", "operator": "EQUALS", "value": "Invoice"},
        {"field": "total_amount", "operator": "GREATER_THAN", "value": "1000"},
    ]}],
    "groups_operator": "AND",
    "actions": [
        {"action_type": "notify.role", "config": {"roles": ["ADMIN"], "title": "Large invoice: {{document.filename}}",
                                                  "message": "Sent to review by {{rule.name}}."}},
        {"action_type": "review.escalate", "config": {"reason": "Invoice above the 1,000 review threshold"}},
    ],
    "else_actions": [
        {"action_type": "work_item.mutate", "config": {"target_field": "summary",
                                                       "target_value": "Auto-cleared: below threshold"}},
    ],
}


def _invoice(engines: Engines, number: str, total: str) -> str:
    return str(engines.process(f"{number}.pdf", [["ACME SUPPLIES LTD", "TAX INVOICE", f"Invoice Number: {number}",
                                                   f"Total: {total} INR"]], marker=number, classification="Invoice",
                               entities={"vendor_name": "Acme Supplies Ltd", "invoice_number": number,
                                         "total_amount": total}))


def test_a_conditional_rule_runs_both_branches_and_is_traced(engines: Engines) -> None:
    _invoice(engines, "INV-AU-0", "10.00")  # a first document, so the builder has fields to offer

    assert engines.post("/automation/rules", RULE, as_user=engines.tenant.contributor).status_code == 403
    created = engines.post("/automation/rules", RULE)
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["is_active"] is True, rule
    listed = {r["id"]: r for r in engines.get("/automation/rules").json()}
    assert listed[rule["id"]]["is_active"] is True
    assert listed[rule["id"]]["triggers"] == ["document.completed"]

    big = _invoice(engines, "INV-AU-1", "5000.00")
    small = _invoice(engines, "INV-AU-2", "50.00")

    notifications = engines.get("/notifications", as_user=engines.tenant.org_admin)
    assert notifications.status_code == 200, notifications.text
    payload = notifications.json()
    rows = payload if isinstance(payload, list) else payload.get("items", [])
    titles = [n.get("title", "") for n in rows]
    assert any("INV-AU-1" in t for t in titles), titles
    assert not any("INV-AU-2" in t for t in titles), titles

    review = engines.get("/review", params={"kind": "EXTRACTION"}).json()["items"]
    assert [str(i["work_item_id"]) for i in review] == [big], review

    assert engines.item(small).summary == "Auto-cleared: below threshold"
    assert engines.item(big).summary != "Auto-cleared: below threshold"

    executions = engines.get("/automation/executions", params={"rule_id": rule["id"]}).json()
    items = executions if isinstance(executions, list) else executions.get("items", [])
    mine = [e for e in items if str(e.get("rule_id")) == rule["id"]]
    assert {str(e.get("work_item_id")) for e in mine} == {big, small}, mine  # the warm-up predates the rule
    traced = [e for e in mine if str(e.get("work_item_id")) == big]
    assert traced and traced[0]["status"] in ("COMPLETED", "SUCCEEDED", "SUCCESS"), traced
    nodes = engines.get(f"/automation/executions/{traced[0]['id']}/nodes").json()
    node_list = nodes if isinstance(nodes, list) else nodes.get("items", nodes.get("nodes", []))
    kinds = [n.get("action_type") or n.get("node_type") or n.get("kind") for n in node_list]
    assert any("notify" in str(k) for k in kinds) and any("review" in str(k) for k in kinds), node_list

    off = engines.patch(f"/automation/rules/{rule['id']}", {"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False
    before = len(mine)
    _invoice(engines, "INV-AU-3", "7000.00")
    after = engines.get("/automation/executions", params={"rule_id": rule["id"]}).json()
    after_items = after if isinstance(after, list) else after.get("items", [])
    assert len([e for e in after_items if str(e.get("rule_id")) == rule["id"]]) == before


def test_a_new_workspace_can_build_data_conditions_before_its_first_document(engines: Engines) -> None:
    fields = {f["key"]: f["type"] for f in engines.get("/automation/catalog").json()["document_fields"]}
    assert fields.get("classification_details.document_classification") == "string", fields
    assert fields.get("total_amount") == "number", fields
    assert engines.post("/automation/rules", RULE).status_code == 201
