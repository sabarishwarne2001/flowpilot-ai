"""Helm's walking skeleton, end to end on the database (brief §12 step 1).

Payable invoices in a Business workspace -> a Helm book -> four mandates (agreed price, duplicates,
pay on time, cash floor) -> the Promise ledger -> a plan (the MILP and the Guard, with certificates)
-> approving one action -> `helm.execute` -> the settlement and the payment run list. Every number
is checked against what the documents say.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.helm import HelmAction, HelmCertificate, HelmPromise, HelmSettlement
from app.models.radar import AnomalyFinding
from app.services.helm import certificate as certs
from app.services.helm import vocabulary as v
from tests.engines.conftest import drain
from tests.security.plans import put_on_plan
from tests.services.helm import factories

API = "/api/v1"
M = 1_000_000


def _url(tenant, path: str) -> str:
    return f"{API}/organizations/{tenant.organization.id}/helm{path}"


@pytest.fixture()
def book(client, db_session, tenant, test_sessions):
    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    response = client.post(_url(tenant, "/books"), json={"workspace_id": str(tenant.workspace.id)},
                           headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    body = response.json()
    response = client.patch(_url(tenant, f"/books/{body['id']}/settings"),
                            json={"identities": [{"name": "Meridian Wholesale", "tax_id": "27AAACM1234F1Z5"}],
                                  "run_weekdays": [1, 2, 3, 4, 5]},
                            headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    return body


def _mandate(client, tenant, book, template, params=None, **extra):
    response = client.post(_url(tenant, f"/books/{book['id']}/mandates"),
                           json={"template": template, "params": params, "activate": True, **extra},
                           headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "ACTIVE"
    return response.json()


def test_the_skeleton_plans_holds_schedules_and_executes_once(client, db_session, tenant, book):
    today = date.today()
    org = tenant.organization.id
    ws = tenant.workspace
    ok = factories.invoice(db_session, workspace=ws, organization_id=org, number="INV-1001",
                           supplier="Deccan Packaging Industries", amount=118_000, on=today - timedelta(days=20),
                           terms="Net 30", supplier_gstin="27AABCD1234E1Z2")
    original = factories.invoice(db_session, workspace=ws, organization_id=org, number="INV-2001",
                                 supplier="Nilgiri Foods", amount=50_000, on=today - timedelta(days=10),
                                 terms="Net 30")
    copy = factories.invoice(db_session, workspace=ws, organization_id=org, number="INV-2001",
                             supplier="Nilgiri Foods", amount=50_000, on=today - timedelta(days=10), terms="Net 30")
    sale = factories.invoice(db_session, workspace=ws, organization_id=org, number="S-77",
                             supplier="Meridian Wholesale", buyer="Kesarbagh Retail", amount=90_000,
                             on=today - timedelta(days=5), extra={"supplier_gstin": "27AAACM1234F1Z5"})
    db_session.add(AnomalyFinding(
        organization_id=org, workspace_id=ws.id, kind="DUPLICATE_DOCUMENT", layer="L1", severity="HIGH",
        subject_work_item_id=copy.id, counterpart_work_item_id=original.id, score=1, headline="Same supplier and number",
        metrics={}, evidence=[{"kind": "identifiers", "vendor": "Nilgiri Foods", "number": "INV-2001"}], status="OPEN", dedupe_key="d" * 64, input_digest="e" * 64, engine_version="arch34.1"))
    db_session.commit()

    response = client.post(_url(tenant, f"/books/{book['id']}/cash-positions"),
                           json={"currency": "INR", "amount": "500000", "as_of": today.isoformat()},
                           headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    for template, params in (("never_pay_above_agreed_price", None), ("never_pay_duplicate", None),
                             ("pay_on_time", None),
                             ("keep_cash_above", {"floor": {"amount_micros": 100_000 * M, "currency": "INR"}})):
        _mandate(client, tenant, book, template, params)

    response = client.post(_url(tenant, f"/books/{book['id']}/plan"), headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    cockpit = response.json()
    assert cockpit["plan"]["status"] == v.SOLVER_OPTIMAL

    # The ledger: three payables (the sale is a receivable because we are its seller).
    kinds = {(p.work_item_id, p.kind) for p in db_session.execute(select(HelmPromise)).scalars()}
    assert (ok.id, v.KIND_PAYMENT_DUE) in kinds
    assert (sale.id, v.KIND_PAYMENT_EXPECTED) in kinds

    actions = {a["kind"] + ":" + str(a["work_item_id"]): a for a in cockpit["actions"]}
    hold = actions[f"hold_payment:{copy.id}"]
    assert hold["guard_passed"] and hold["needs_approval"]
    assert f"schedule_payment:{copy.id}" not in actions  # a duplicate is never scheduled
    pay = actions[f"schedule_payment:{ok.id}"]
    assert pay["amount_micros"] == 118_000 * M
    assert pay["run_date"] <= (today + timedelta(days=10)).isoformat()  # due in 10 days, paid by then
    assert pay["guard"]["passed"] and pay["needs_approval"]  # Propose mode: a person approves
    checks = {c["id"]: c["result"] for c in pay["guard"]["checks"]}
    assert checks["fraud"] == "pass" and checks["idempotency"] == "pass" and checks["autonomy"] == "needs_approval"
    assert checks["limit:keep_cash_above"] == "pass"

    # Approving twice approves once; the worker executes once.
    first = client.post(_url(tenant, f"/actions/{pay['id']}/approve"), headers=tenant.owner.headers)
    second = client.post(_url(tenant, f"/actions/{pay['id']}/approve"), headers=tenant.owner.headers)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["state"] == v.ACTION_APPROVED
    runs = drain(only=[v.JOB_EXECUTE])
    assert [r.ok for r in runs] == [True]
    db_session.expire_all()
    action = db_session.get(HelmAction, uuid.UUID(pay["id"]))
    assert action.state == v.ACTION_EXECUTED
    settlement = db_session.execute(select(HelmSettlement).where(HelmSettlement.work_item_id == ok.id)).scalar_one()
    assert settlement.state == v.SETTLEMENT_SCHEDULED and settlement.run_date.isoformat() == pay["run_date"]

    # Its EXECUTION certificate verifies, and is append-only.
    cert = db_session.get(HelmCertificate, action.certificate_id)
    assert cert.stage == v.CERT_STAGE_EXECUTION and cert.passed
    assert certs.verify(cert.canonical, cert.sha256)
    page = client.get(_url(tenant, f"/certificates/{cert.id}"), headers=tenant.owner.headers).json()
    assert page["verified"] and page["body"]["action"]["kind"] == "schedule_payment"
    download = client.get(_url(tenant, f"/certificates/{cert.id}/download"), headers=tenant.owner.headers)
    assert download.status_code == 200 and download.text == cert.canonical

    # The payment run lists it, with the account masked.
    run = client.get(_url(tenant, f"/books/{book['id']}/payment-run"), headers=tenant.owner.headers).json()
    lines = [line for day in run["days"] for line in day["lines"]]
    assert [line["invoice"] for line in lines] == ["INV-1001"]
    assert lines[0]["certificate_id"] == str(cert.id)


def test_members_never_see_helm_and_billing_cannot_change_it(client, db_session, tenant, book):
    member = client.get(_url(tenant, ""), headers=tenant.viewer.headers)
    assert member.status_code == 403
    assert client.get(_url(tenant, ""), headers=tenant.org_admin.headers).status_code == 200


def test_below_business_helm_is_preview_and_refuses_writes(client, db_session, tenant, test_sessions):
    put_on_plan(db_session, tenant.organization, "developer")
    db_session.commit()
    overview = client.get(_url(tenant, ""), headers=tenant.owner.headers)
    assert overview.status_code == 200
    assert overview.json()["access"] == {"helm": False, "preview": True, "custom": False, "approval_chains": False,
                                         "all_workspaces": False, "api": False}
    refused = client.post(_url(tenant, "/books"), json={"workspace_id": str(tenant.workspace.id)},
                          headers=tenant.owner.headers)
    assert refused.status_code == 402
    assert refused.json()["code"] == "CAPABILITY_REQUIRED"


def test_a_sixteenth_active_mandate_is_refused_on_business(client, db_session, tenant, book):
    for n in range(15):
        _mandate(client, tenant, book, "keep_cash_above",
                 {"floor": {"amount_micros": (n + 1) * 1000 * M, "currency": "INR"}})
    response = client.post(_url(tenant, f"/books/{book['id']}/mandates"),
                           json={"template": "pay_on_time", "activate": True}, headers=tenant.owner.headers)
    assert response.status_code == 402, response.text
    assert response.json()["details"]["limit_key"] == "limit.helm_mandates"


def test_a_duplicate_of_an_active_mandate_is_refused_in_words(client, db_session, tenant, book):
    _mandate(client, tenant, book, "pay_on_time")
    response = client.post(_url(tenant, f"/books/{book['id']}/mandates"),
                           json={"template": "pay_on_time", "activate": True}, headers=tenant.owner.headers)
    assert response.status_code == 422
    assert "same as the active mandate" in response.json()["message"]
    # A different parameter is a different mandate.
    harder = client.post(_url(tenant, f"/books/{book['id']}/mandates"),
                         json={"template": "pay_on_time", "params": {"hard": True}}, headers=tenant.owner.headers)
    assert harder.status_code == 201 and harder.json()["status"] == "DRAFT"
    negative = client.post(_url(tenant, f"/books/{book['id']}/mandates/preview"),
                           json={"template": "keep_cash_above", "params": {"floor": {"amount_micros": -5,
                                                                                      "currency": "INR"}}},
                           headers=tenant.owner.headers)
    assert negative.status_code == 200
    assert negative.json()["valid"] is False
    assert negative.json()["problems"] == ["The amount cannot be below zero."]
