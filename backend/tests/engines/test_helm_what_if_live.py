"""What-if on real Postgres through the API (`POST …/helm/books/{bid}/what-if`).

A Business book with two supplier invoices, a customer invoice, ₹5 lakh of cash, "pay on time" and a
₹1 lakh cash floor, planned once. Then:

* a what-if writes nothing: plans, actions, certificates, audit rows, notices, jobs, settlements,
  counterparties and settings are counted before and after, and the real plan is unchanged;
* a cash drop lowers the chance of staying above the floor, and the floor mandate's change is named;
* holding an invoice takes its payment out of the scenario's plan;
* a bad scenario is refused in words; an invoice from another book, a customer's invoice or an
  unknown id cannot be held;
* access: members are refused (403), a book on a plan without Helm is refused like Re-plan (402),
  another organization's book is not found, and a Billing person who cannot read the workspace sees
  the states but not the details and cannot hold its invoices.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.models.organization import OrganizationRole
from app.models.workspace import Workspace, WorkspaceStatus
from tests.conftest import _make_user, _seat
from tests.security.plans import put_on_plan
from tests.services.helm import factories

API = "/api/v1"
M = 1_000_000
TABLES = ("helm_plans", "helm_actions", "helm_certificates", "helm_settlements", "helm_counterparties",
          "helm_settings", "helm_cash_positions", "helm_tasks", "helm_value_events", "helm_backtests", "audit_logs",
          "notifications", "jobs")


def _url(tenant, path: str) -> str:
    return f"{API}/organizations/{tenant.organization.id}/helm{path}"


def _counts(db_session) -> dict[str, int]:
    db_session.expire_all()
    return {t: db_session.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in TABLES}  # noqa: S608


def _start_book(client, tenant, workspace_id) -> dict:
    response = client.post(_url(tenant, "/books"), json={"workspace_id": str(workspace_id)},
                           headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    body = response.json()
    response = client.patch(_url(tenant, f"/books/{body['id']}/settings"),
                            json={"identities": [{"name": "Meridian Wholesale", "tax_id": "27AAACM1234F1Z5"}],
                                  "run_weekdays": [1, 2, 3, 4, 5]},
                            headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    return body


@pytest.fixture()
def book(client, db_session, tenant, test_sessions):
    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    body = _start_book(client, tenant, tenant.workspace.id)
    today = date.today()
    org, ws = tenant.organization.id, tenant.workspace
    factories.invoice(db_session, workspace=ws, organization_id=org, number="INV-1001",
                      supplier="Deccan Packaging Industries", amount=118_000, on=today - timedelta(days=20),
                      terms="Net 30", supplier_gstin="27AABCD1234E1Z2")
    factories.invoice(db_session, workspace=ws, organization_id=org, number="INV-1002", supplier="Nilgiri Foods",
                      amount=60_000, on=today - timedelta(days=15), terms="Net 30")
    factories.invoice(db_session, workspace=ws, organization_id=org, number="S-77", supplier="Meridian Wholesale",
                      buyer="Kesarbagh Retail", amount=90_000, on=today - timedelta(days=5),
                      extra={"supplier_gstin": "27AAACM1234F1Z5"})
    db_session.commit()
    response = client.post(_url(tenant, f"/books/{body['id']}/cash-positions"),
                           json={"currency": "INR", "amount": "500000", "as_of": today.isoformat()},
                           headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    for template, params in (("pay_on_time", None),
                             ("keep_cash_above", {"floor": {"amount_micros": 100_000 * M, "currency": "INR"}})):
        response = client.post(_url(tenant, f"/books/{body['id']}/mandates"),
                               json={"template": template, "params": params, "activate": True},
                               headers=tenant.owner.headers)
        assert response.status_code == 201, response.text
    response = client.post(_url(tenant, f"/books/{body['id']}/plan"), headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    return body


def _promises(client, tenant, book, headers=None) -> dict[str, dict]:
    response = client.get(_url(tenant, f"/books/{book['id']}/ledger"), headers=headers or tenant.owner.headers)
    assert response.status_code == 200, response.text
    return {(p.get("details") or {}).get("document_number") or p["title"]: p for p in response.json()["promises"]
            if p["kind"] in ("PAYMENT_DUE", "PAYMENT_EXPECTED")}


def _what_if(client, tenant, book, body, headers=None):
    return client.post(_url(tenant, f"/books/{book['id']}/what-if"), json=body,
                       headers=headers or tenant.owner.headers)


def test_a_what_if_writes_nothing_and_a_cash_drop_lowers_the_chance_of_holding_the_floor(client, db_session,
                                                                                          tenant, book):
    cockpit = client.get(_url(tenant, f"/books/{book['id']}/cockpit"), headers=tenant.owner.headers).json()
    before = _counts(db_session)

    response = _what_if(client, tenant, book, {"cash_change_micros": -450_000 * M})
    assert response.status_code == 200, response.text
    result = response.json()
    assert _counts(db_session) == before  # no plan, action, certificate, audit row, notice or job
    after = client.get(_url(tenant, f"/books/{book['id']}/cockpit"), headers=tenant.owner.headers).json()
    assert after["plan"]["id"] == cockpit["plan"]["id"] and after["plan"]["version"] == cockpit["plan"]["version"]
    assert [a["id"] for a in after["actions"]] == [a["id"] for a in cockpit["actions"]]

    assert result["saved"] is False and result["inputs"]["currency"] == "INR"
    assert result["floors"] == {"INR": 100_000 * M}
    base, what = result["baseline"]["floor_probability"]["INR"], result["scenario"]["floor_probability"]["INR"]
    assert what < base
    assert result["baseline"]["cash_today"] == {"INR": 500_000 * M}
    assert result["scenario"]["cash_today"] == {"INR": 50_000 * M}
    assert len(result["baseline"]["cash_paths"]["INR"]) == 91 and len(result["scenario"]["cash_paths"]["INR"]) == 91
    changes = {c["template"]: c for c in result["mandates"]}
    assert changes["keep_cash_above"]["scenario"]["state"] == "breached"
    assert changes["keep_cash_above"]["baseline"]["state"] in ("holding", "drifting")
    assert result["summary"].startswith("If cash were ₹4.5 lakh lower today, INR cash would stay above the ₹1 lakh "
                                        "floor in ")
    assert "Keep cash above a floor would go from" in result["summary"]
    assert _counts(db_session) == before


def test_holding_an_invoice_takes_its_payment_out_of_the_scenario(client, db_session, tenant, book):
    promises = _promises(client, tenant, book)
    held = promises["INV-1001"]["id"]
    before = _counts(db_session)
    response = _what_if(client, tenant, book, {"hold_promise_ids": [held], "horizon_days": 30})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["inputs"]["hold_promise_ids"] == [held]
    assert result["baseline"]["payments"] == result["scenario"]["payments"] + 1
    assert "1 invoice was held (INV-1001)" in result["summary"]
    assert len(result["scenario"]["cash_paths"]["INR"]) == 31
    assert _counts(db_session) == before


def test_a_bad_scenario_is_refused_in_words_and_only_this_books_invoices_can_be_held(client, db_session, tenant,
                                                                                    book):
    for body, words in (({}, "Tell Helm what to change"),
                        ({"delay_receivables_days": 91}, "Customers can be delayed by 0 to 90 days."),
                        ({"delay_receivables_days": "soon"}, "The delay is a whole number of days."),
                        ({"horizon_days": 1.5, "delay_receivables_days": 5}, "How far ahead to look is a whole number"),
                        ({"delay_receivables_days": 10, "horizon_days": 365}, "Helm looks 7 to 90 days ahead"),
                        ({"cash_change_micros": -5 * M, "currency": "USD"}, "This book has no USD cash position"),
                        ({"hold_promise_ids": ["not-an-id"]}, "Invoice 1 of the list to hold is not an invoice "
                                                              "you owe in this book.")):
        response = _what_if(client, tenant, book, body)
        assert response.status_code == 422, (body, response.text)
        assert words in response.json()["message"], response.json()
        assert response.json()["details"]["reason"] == "WHAT_IF_INVALID"

    # Another workspace's book, in the same organization: its invoice is not one of this book's.
    finance = Workspace(organization_id=tenant.organization.id, slug="finance", workspace_name="Finance",
                        status=WorkspaceStatus.ACTIVE)
    db_session.add(finance)
    db_session.commit()
    other = _start_book(client, tenant, finance.id)
    factories.invoice(db_session, workspace=finance, organization_id=tenant.organization.id, number="FIN-9",
                      supplier="Zenith Steel", amount=50_000, on=date.today() - timedelta(days=12))
    db_session.commit()
    assert client.post(_url(tenant, f"/books/{other['id']}/plan"), headers=tenant.owner.headers).status_code == 200
    theirs = _promises(client, tenant, other)["FIN-9"]["id"]
    customer = _promises(client, tenant, book)["S-77"]["id"]
    response = _what_if(client, tenant, book, {"hold_promise_ids": [theirs, customer, str(uuid.uuid4())]})
    assert response.status_code == 422, response.text
    assert response.json()["details"]["problems"] == [
        "Invoice 1 of the list to hold is not an invoice you owe in this book.",
        "Invoice 2 of the list to hold is not an invoice you owe in this book.",
        "Invoice 3 of the list to hold is not an invoice you owe in this book."]


def test_who_may_ask_what_if(client, db_session, tenant, book):
    body = {"delay_receivables_days": 30}
    # Members never see Helm; another organization's people cannot reach this one.
    assert _what_if(client, tenant, book, body, headers=tenant.viewer.headers).status_code == 403
    assert _what_if(client, tenant, book, body, headers=tenant.other_org_member.headers).status_code in (403, 404)
    missing = client.post(_url(tenant, f"/books/{uuid.uuid4()}/what-if"), json=body, headers=tenant.owner.headers)
    assert missing.status_code == 404

    # A Billing person with no grant to the workspace: the states, never the details, and no holds.
    billing = _make_user(db_session, f"billing-{uuid.uuid4().hex[:8]}@acme.com")
    _seat(db_session, tenant.organization, billing, OrganizationRole.BILLING)
    db_session.commit()
    shown = _what_if(client, tenant, book, {"cash_change_micros": -450_000 * M}, headers=billing.headers)
    assert shown.status_code == 200, shown.text
    result = shown.json()
    assert result["readable"] is False and result["mandates"]
    assert all(c["baseline"]["detail"] == "hidden" and c["scenario"]["detail"] == "hidden" for c in result["mandates"])
    held = _promises(client, tenant, book)["INV-1001"]["id"]
    refused = _what_if(client, tenant, book, {"hold_promise_ids": [held]}, headers=billing.headers)
    assert refused.status_code == 403 and refused.json()["details"]["reason"] == "HELM_NO_ACCESS"
    assert "INV-1001" not in refused.text

    # Without capability.helm the book is Helm preview's: refused like Re-plan, before anything is read.
    put_on_plan(db_session, tenant.organization, "developer")
    db_session.commit()
    before = _counts(db_session)
    preview = _what_if(client, tenant, book, body)
    assert preview.status_code == 402, preview.text
    assert preview.json()["code"] == "CAPABILITY_REQUIRED"
    replan = client.post(_url(tenant, f"/books/{book['id']}/plan"), headers=tenant.owner.headers)
    assert replan.status_code == 402
    assert _counts(db_session) == before
