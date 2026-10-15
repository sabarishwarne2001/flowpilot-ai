"""Custom condition mandates on real Postgres, through the API (Enterprise, `capability.helm_custom`).

A Business book can neither create, preview as valid, nor backtest one: it is refused in words. An
Enterprise book creates "Large invoices from new suppliers" (amount above ₹5 lakh and a supplier with
no earlier invoice), the plan holds the matching invoice and leaves the others to be paid, the hold
runs once approved with the mandate's own reason code, and the Guard refuses paying the invoice.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.models.helm import HelmAction, HelmPromise, HelmSettlement
from app.services.helm import executor
from app.services.helm import vocabulary as v
from tests.engines.conftest import drain
from tests.security.plans import put_on_plan
from tests.services.helm import factories

API = "/api/v1"
M = 1_000_000
LAKH = 100_000 * M
RULE = {"name": "Large invoices from new suppliers", "match": "all", "outcome": "hold", "conditions": [
    {"field": "amount", "op": ">", "value": {"amount_micros": 5 * LAKH, "currency": "INR"}},
    {"field": "new_supplier", "op": "=", "value": True}]}


def _url(tenant, path: str) -> str:
    return f"{API}/organizations/{tenant.organization.id}/helm{path}"


def _book(client, db_session, tenant, plan: str) -> dict:
    put_on_plan(db_session, tenant.organization, plan)
    db_session.commit()
    response = client.post(_url(tenant, "/books"), json={"workspace_id": str(tenant.workspace.id)},
                           headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    book = response.json()
    response = client.patch(_url(tenant, f"/books/{book['id']}/settings"),
                            json={"identities": [{"name": "Meridian Wholesale", "tax_id": "27AAACM1234F1Z5"}],
                                  "run_weekdays": [1, 2, 3, 4, 5]}, headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    return book


def test_a_business_book_cannot_create_preview_or_backtest_a_custom_condition(client, db_session, tenant,
                                                                               test_sessions):
    book = _book(client, db_session, tenant, "business")
    url, headers = _url(tenant, f"/books/{book['id']}/mandates"), tenant.owner.headers
    refused = client.post(url, json={"template": "custom_condition", "params": RULE, "activate": True},
                          headers=headers)
    assert refused.status_code == 422, refused.text
    assert refused.json()["message"] == '"Custom condition" is part of the Enterprise plan.'
    preview = client.post(f"{url}/preview", json={"template": "custom_condition", "params": RULE},
                          headers=headers).json()
    assert preview["valid"] is False
    assert preview["problems"] == ['"Custom condition" is part of the Enterprise plan.']
    assert client.get(url, headers=headers).json()["mandates"] == []
    backtest = client.post(_url(tenant, "/backtests"), json={"book_id": book["id"], "months": 3, "mandates": [
        {"template": "custom_condition", "params": RULE}]}, headers=headers)
    assert backtest.status_code == 422, backtest.text
    assert backtest.json()["message"] == '"Custom condition" is part of the Enterprise plan.'
    library = client.get(_url(tenant, "/library"), headers=headers).json()
    card = next(t for t in library["templates"] if t["key"] == "custom_condition")
    assert card["available"] is False and card["plan"] == "enterprise" and card["editor"]["max_conditions"] == 8


def test_enterprise_holds_a_matching_invoice_and_the_guard_refuses_paying_it(client, db_session, tenant,
                                                                              test_sessions):
    book = _book(client, db_session, tenant, "enterprise")
    today = date.today()
    org, ws = tenant.organization.id, tenant.workspace
    # Deccan has invoiced before (40 days ago), so its large invoice is not from a new supplier.
    earlier = factories.invoice(db_session, workspace=ws, organization_id=org, number="DP-90",
                                supplier="Deccan Packaging", amount=20_000, on=today - timedelta(days=41),
                                terms="Net 60")
    earlier.created_at = datetime.now(timezone.utc) - timedelta(days=40)
    regular = factories.invoice(db_session, workspace=ws, organization_id=org, number="DP-91",
                                supplier="Deccan Packaging", amount=600_000, on=today - timedelta(days=5),
                                terms="Net 30")
    newcomer = factories.invoice(db_session, workspace=ws, organization_id=org, number="ZM-1",
                                 supplier="Zenith Metals", amount=700_000, on=today - timedelta(days=3),
                                 terms="Net 30")
    db_session.commit()
    response = client.post(_url(tenant, f"/books/{book['id']}/cash-positions"),
                           json={"currency": "INR", "amount": "5000000", "as_of": today.isoformat()},
                           headers=tenant.owner.headers)
    assert response.status_code == 201, response.text

    url, headers = _url(tenant, f"/books/{book['id']}/mandates"), tenant.owner.headers
    bad = client.post(f"{url}/preview", json={"template": "custom_condition", "params": {
        **RULE, "conditions": [{"field": "vendor_score", "op": ">", "value": 3}]}}, headers=headers).json()
    assert bad["valid"] is False and bad["problems"][0].startswith('Condition 1: Helm cannot test "vendor_score"')
    created = client.post(url, json={"template": "custom_condition", "params": RULE, "activate": True},
                          headers=headers)
    assert created.status_code == 201, created.text
    mandate = created.json()
    assert mandate["status"] == "ACTIVE" and mandate["title"] == "Large invoices from new suppliers"
    assert mandate["readback"]["sentence"] == ("Hold any invoice where the amount is above ₹5 lakh and the supplier "
                                               "is new; release it once that is no longer true.")
    paying = client.post(url, json={"template": "pay_on_time", "activate": True}, headers=headers)
    assert paying.status_code == 201, paying.text

    planned = client.post(_url(tenant, f"/books/{book['id']}/plan"), headers=headers)
    assert planned.status_code == 200, planned.text
    actions = {f"{a['kind']}:{a['work_item_id']}": a for a in planned.json()["actions"]}
    hold = actions[f"hold_payment:{newcomer.id}"]
    assert hold["guard_passed"] and hold["mandate_id"] == mandate["id"]
    assert "Large invoices from new suppliers" in hold["title"]
    assert f"schedule_payment:{newcomer.id}" not in actions  # never planned for payment
    assert f"hold_payment:{regular.id}" not in actions and f"schedule_payment:{regular.id}" in actions

    # A payment of the matching invoice that reaches execution is blocked by the Guard.
    promise = db_session.execute(select(HelmPromise).where(HelmPromise.work_item_id == newcomer.id,
                                                           HelmPromise.kind == v.KIND_PAYMENT_DUE)).scalar_one()
    pay = HelmAction(organization_id=org, book_id=uuid.UUID(book["id"]), workspace_id=ws.id, work_item_id=newcomer.id,
                     promise_id=promise.id, kind=v.ACTION_SCHEDULE_PAYMENT,
                     idempotency_key=f"helm:test:{uuid.uuid4().hex}", state=v.ACTION_APPROVED,
                     autonomy=v.AUTONOMY_PROPOSE, needs_approval=True, guard_passed=True, title="Pay Zenith Metals",
                     explanation="A person scheduled it.", params={}, undoable=True, amount_micros=700_000 * M,
                     currency="INR", run_date=today + timedelta(days=1),
                     approvals=[{"role": "OWNER", "at": datetime.now(timezone.utc).isoformat()}])
    db_session.add(pay)
    db_session.commit()
    executor.execute(db_session, action_id=pay.id)
    db_session.commit()
    db_session.refresh(pay)
    assert pay.state == v.ACTION_BLOCKED
    check_id = f"limit:custom_condition:{mandate['id']}"
    assert check_id in pay.result["failed"]
    failed = next(c for c in pay.guard["checks"] if c["id"] == check_id)
    assert failed["detail"] == ('It matches "Large invoices from new suppliers": the amount is above ₹5 lakh and '
                                "the supplier is new, so Helm holds it.")

    # The hold runs once approved, under this mandate's own reason code.
    approved = client.post(_url(tenant, f"/actions/{hold['id']}/approve"), headers=headers)
    assert approved.status_code == 200, approved.text
    runs = drain(only=[v.JOB_EXECUTE])
    assert runs and all(r.ok for r in runs)
    db_session.expire_all()
    settlement = db_session.execute(select(HelmSettlement).where(HelmSettlement.work_item_id == newcomer.id)
                                    ).scalar_one()
    assert settlement.state == v.SETTLEMENT_HELD
    assert settlement.evidence["reason"] == f"CUSTOM_CONDITION:{mandate['id']}"

    # Renaming the rule is a new version, and the title follows the name.
    renamed = client.patch(f"{url}/{mandate['id']}", json={"params": {**RULE, "name": "Big first invoices"}},
                           headers=headers)
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Big first invoices" and renamed.json()["version"] == 2


def test_enterprise_approval_rule_asks_a_person_before_paying(client, db_session, tenant, test_sessions):
    outcome = "approval"
    book = _book(client, db_session, tenant, "enterprise")
    today = date.today()
    org, ws = tenant.organization.id, tenant.workspace
    item = factories.invoice(db_session, workspace=ws, organization_id=org, number="ZM-7",
                             supplier="Zenith Metals", amount=700_000, on=today - timedelta(days=3), terms="Net 30")
    db_session.commit()
    client.post(_url(tenant, f"/books/{book['id']}/cash-positions"),
                json={"currency": "INR", "amount": "5000000", "as_of": today.isoformat()},
                headers=tenant.owner.headers)
    url, headers = _url(tenant, f"/books/{book['id']}/mandates"), tenant.owner.headers
    created = client.post(url, json={"template": "custom_condition", "params": {**RULE, "outcome": outcome},
                                     "activate": True}, headers=headers)
    assert created.status_code == 201, created.text
    client.post(url, json={"template": "pay_on_time", "activate": True, "autonomy": {
        "mode": "act", "act_cap": {"amount_micros": 10_000_000 * M, "currency": "INR"}}}, headers=headers)
    actions = client.post(_url(tenant, f"/books/{book['id']}/plan"), headers=headers).json()["actions"]
    pay = next(a for a in actions if a["kind"] == "schedule_payment" and a["work_item_id"] == str(item.id))
    checks = {c["id"]: c["result"] for c in pay["guard"]["checks"]}
    # Act mode within its cap would pay alone; the custom rule makes a person approve it.
    assert checks["autonomy"] == "pass"
    assert checks[f"limit:custom_condition:{created.json()['id']}"] == "needs_approval"
    assert pay["guard_passed"] and pay["needs_approval"]


def test_after_a_move_to_business_a_custom_condition_is_paused_or_archived_but_never_changed(
        client, db_session, tenant, test_sessions):
    """Made on Enterprise, then the plan moves to Business: the rule keeps protecting until a person
    pauses or archives it, and it can be neither edited nor switched back on (decision in the hand-over)."""
    book = _book(client, db_session, tenant, "enterprise")
    item = factories.invoice(db_session, workspace=tenant.workspace, organization_id=tenant.organization.id,
                             number="ZM-9", supplier="Zenith Metals", amount=700_000,
                             on=date.today() - timedelta(days=3), terms="Net 30")
    db_session.commit()
    url, headers = _url(tenant, f"/books/{book['id']}/mandates"), tenant.owner.headers
    created = client.post(url, json={"template": "custom_condition", "params": RULE, "activate": True},
                          headers=headers)
    assert created.status_code == 201, created.text
    mandate = created.json()
    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()

    listed = client.get(url, headers=headers).json()["mandates"]
    assert [(m["id"], m["status"]) for m in listed] == [(mandate["id"], "ACTIVE")]
    planned = client.post(_url(tenant, f"/books/{book['id']}/plan"), headers=headers)
    assert planned.status_code == 200, planned.text
    assert [a["mandate_id"] for a in planned.json()["actions"]
            if a["kind"] == "hold_payment" and a["work_item_id"] == str(item.id)] == [mandate["id"]]
    edited = client.patch(f"{url}/{mandate['id']}", json={"params": {**RULE, "name": "Bigger"}}, headers=headers)
    assert edited.status_code == 422, edited.text
    assert edited.json()["message"] == '"Custom condition" is part of the Enterprise plan.'
    paused = client.post(f"{url}/{mandate['id']}/pause", headers=headers)
    assert paused.status_code == 200 and paused.json()["status"] == "PAUSED", paused.text
    again = client.post(f"{url}/{mandate['id']}/activate", headers=headers)
    assert again.status_code == 422, again.text
    assert again.json()["message"] == '"Custom condition" is part of the Enterprise plan.'
    archived = client.post(f"{url}/{mandate['id']}/archive", headers=headers)
    assert archived.status_code == 200 and archived.json()["status"] == "ARCHIVED", archived.text
    assert client.get(url, headers=headers).json()["mandates"] == []
