"""A developer's key triggers a workflow, and the workflow runs (campaign session 3, coverage ledger).

`POST /api/v1/public/workflows/{rule_id}/trigger` answers 202 QUEUED: it proves the rule exists,
is active and belongs to the workspace, and raises a `work_item.field_changed` event that the
automation engine resolves on its own. The gateway tests covered the refusals; nothing showed a
triggered rule actually running. Here a key with `public_workflows:write` triggers a rule on a
processed document, the worker drains, and the rule's execution and its effect are there. A
retry inside the same minute does not run it twice, a paused rule is refused, and a key without
the scope cannot trigger anything.
"""

from __future__ import annotations

from tests.engines.conftest import Engines, drain

API = "/api/v1"

RULE = {
    "name": "Ping finance from the ERP",
    "triggers": ["document.field_changed"],
    "condition_groups": [],
    "groups_operator": "AND",
    "actions": [
        {"action_type": "notify.role", "config": {"roles": ["ADMIN"], "title": "ERP asked about {{document.filename}}",
                                                  "message": "Raised through the public API."}},
    ],
}


def _key(engines: Engines, scopes: list[str]) -> str:
    issued = engines.post("/developer/keys", {
        "name": f"erp {'-'.join(scopes)}", "scopes": scopes, "tier_key": "FREE", "enable_public_api": True,
    }, org=True, as_user=engines.tenant.org_admin)
    assert issued.status_code == 201, issued.text
    return issued.json()["token"]


def _trigger(engines: Engines, token: str, rule_id: str, item: str):
    return engines.client.post(
        f"{API}/public/workflows/{rule_id}/trigger",
        json={"workspace_id": str(engines.ws), "work_item_id": item},
        headers={"Authorization": f"Bearer {token}"},
    )


def _runs(engines: Engines, rule_id: str) -> list[dict]:
    page = engines.get("/automation/executions", params={"rule_id": rule_id}).json()
    items = page if isinstance(page, list) else page.get("items", [])
    return [run for run in items if str(run.get("rule_id")) == rule_id]


def test_a_triggered_workflow_runs_once_and_only_when_allowed(engines: Engines) -> None:
    engines.plan("business")
    item = str(engines.process("PO-API-7.pdf", [["PURCHASE ORDER", "PO Number: PO-API-7", "Total: 120.00 USD"]],
                               marker="PO-API-7", classification="Purchase Order"))
    created = engines.post("/automation/rules", RULE)
    assert created.status_code == 201, created.text
    rule = created.json()["id"]
    assert _runs(engines, rule) == [], "nothing has asked for it yet"

    token = _key(engines, ["public_workflows:write"])
    first = _trigger(engines, token, rule, item)
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "QUEUED" and first.json()["rule_id"] == rule
    again = _trigger(engines, token, rule, item)
    assert again.status_code == 202 and again.json()["outbox_event_id"] == first.json()["outbox_event_id"], (
        "a retried request inside the minute is the same event"
    )

    drain()
    runs = _runs(engines, rule)
    assert [str(run["work_item_id"]) for run in runs] == [item], runs
    titles = [n.get("title", "") for n in
              (lambda body: body if isinstance(body, list) else body.get("items", []))(
                  engines.get("/notifications", as_user=engines.tenant.org_admin).json())]
    assert any("PO-API-7" in title for title in titles), titles

    reader = _key(engines, ["public_documents:read"])
    assert _trigger(engines, reader, rule, item).status_code == 403, "the key lacks public_workflows:write"

    assert engines.patch(f"/automation/rules/{rule}", {"is_active": False}).status_code == 200
    paused = _trigger(engines, token, rule, item)
    assert paused.status_code == 409, paused.text
    drain()
    assert len(_runs(engines, rule)) == 1, "a paused rule is not run"
