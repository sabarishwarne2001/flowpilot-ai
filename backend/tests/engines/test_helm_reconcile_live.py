"""Supplier statement reconciliation on real Postgres (owner addition B).

1. A supplier's statement of account, uploaded next to their invoice, no longer marks the invoice
   paid or becomes cash (it did: the ledger read every STATEMENT as a bank's). A bank's statement
   still settles and gives cash.
2. The sample company carries Sahyadri's statement with three planted differences. Its plan proposes
   one reconciliation draft naming exactly those, with the balance gap explained to the paisa;
   approving it drafts the email (the sample book never sends).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.storage import reset_storage_driver
from app.models.helm import HelmAction, HelmBook, HelmPromise, HelmSettings
from app.services.helm import ledger_store, reconcile, sample_company, snapshot_store
from app.services.helm import vocabulary as v
from tests.engines.conftest import drain
from tests.security.plans import put_on_plan
from tests.services.helm import factories

M = 1_000_000


@pytest.fixture()
def stubs(monkeypatch, tmp_path):
    from app.services.embedding_service import embedding_service

    monkeypatch.setattr(settings, "ML_STUBS", True)
    monkeypatch.setattr(embedding_service, "_model", None)
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    reset_storage_driver()
    yield
    reset_storage_driver()


def _api(tenant) -> str:
    return f"/api/v1/organizations/{tenant.organization.id}/helm"


def test_a_supplier_statement_is_neither_payment_nor_cash_and_a_bank_statement_still_is(db_session, tenant):
    put_on_plan(db_session, tenant.organization, "business")
    book = HelmBook(organization_id=tenant.organization.id, workspace_id=tenant.workspace.id, kind=v.BOOK_WORKSPACE,
                    name="Engineering", status=v.BOOK_ACTIVE)
    db_session.add(book)
    db_session.flush()
    db_session.add(HelmSettings(book_id=book.id, organization_id=book.organization_id,
                                identities=[{"name": "Meridian Wholesale", "tax_id": "27AAACM1234F1Z5"}]))
    org, ws = tenant.organization.id, tenant.workspace
    today = date.today()
    factories.invoice(db_session, workspace=ws, organization_id=org, number="KP-1182", supplier="Konkan Packaging",
                      amount=50_000, on=today - timedelta(days=20))
    factories.document(db_session, workspace=ws, organization_id=org, role="STATEMENT", vendor="Konkan Packaging",
                       entities={"document_classification": "Statement of Account", "vendor_name": "Konkan Packaging",
                                 "statement_date": (today - timedelta(days=2)).isoformat(), "closing_balance": "50000",
                                 "transactions": [{"date": (today - timedelta(days=20)).isoformat(),
                                                   "description": "Invoice KP-1182", "reference": "KP-1182",
                                                   "debit": "50000.00"}]},
                       on=today - timedelta(days=2), text="STATEMENT OF ACCOUNT\nKonkan Packaging")
    db_session.commit()
    ledger_store.rebuild_workspace(db_session, workspace_id=ws.id, as_of=today)
    db_session.commit()

    def settlement():
        promise = db_session.execute(select(HelmPromise).where(HelmPromise.kind == v.KIND_PAYMENT_DUE)).scalar_one()
        return promise.details["settlement"]["state"]

    assert settlement() == v.SETTLEMENT_UNPAID
    snapshot = snapshot_store.take(db_session, book, as_of=today)
    assert "INR" not in snapshot.cash  # the ₹50,000 we owe them is not our cash
    [statement] = snapshot.supplier_statements
    assert statement.counterparty_name == "Konkan Packaging" and statement.closing_balance_micros == 50_000 * M
    assert reconcile.reconcile(statement, snapshot.payables).agrees

    factories.document(db_session, workspace=ws, organization_id=org, role="STATEMENT",
                       entities={"document_classification": "Bank Statement", "closing_balance": "900000",
                                 "statement_date": (today - timedelta(days=1)).isoformat(),
                                 "transactions": [{"date": (today - timedelta(days=3)).isoformat(),
                                                   "description": "NEFT KONKAN PACKAGING KP-1182",
                                                   "debit": "50000.00"}]},
                       on=today - timedelta(days=1), text="HDFC BANK LTD\nStatement of account")
    db_session.commit()
    ledger_store.rebuild_workspace(db_session, workspace_id=ws.id, as_of=today)
    db_session.commit()
    db_session.expire_all()
    assert settlement() == v.SETTLEMENT_PAID
    snapshot = snapshot_store.take(db_session, book, as_of=today)
    assert snapshot.cash["INR"].amount_micros == 900_000 * M and len(snapshot.supplier_statements) == 1


def test_the_sample_company_proposes_the_planted_reconciliation_and_drafts_the_email(
        client, db_session, tenant, test_sessions, stubs):
    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    headers = tenant.owner.headers
    assert client.post(f"{_api(tenant)}/sample", headers=headers).status_code == 202
    drain(only=[v.JOB_SAMPLE])
    sample = client.get(_api(tenant), headers=headers).json()["sample"]
    db_session.expire_all()
    book = db_session.get(HelmBook, sample["book_id"])
    _, planted = sample_company.plan(date.today())

    [action] = db_session.execute(select(HelmAction).where(
        HelmAction.book_id == book.id, HelmAction.kind == v.ACTION_DRAFT_RECONCILIATION)).scalars().all()
    assert action.state == v.ACTION_PROPOSED and "Sahyadri Packaging Industries" in action.title
    codes = [d["code"] for d in action.params["differences"]]
    assert sorted(c for c in codes if c != reconcile.BALANCE_DIFFERS) == list(planted.statement_differences)
    assert codes[-1] == reconcile.BALANCE_DIFFERS and action.params["unexplained_micros"] == 0
    assert action.params["their_balance_micros"] - action.params["our_balance_micros"] == planted.statement_gap_micros
    for difference in action.params["differences"]:
        assert difference["evidence"], difference  # every difference names its evidence

    approved = client.post(f"{_api(tenant)}/actions/{action.id}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    drain(only=[v.JOB_EXECUTE])
    db_session.expire_all()
    action = db_session.get(HelmAction, action.id)
    assert action.state == v.ACTION_EXECUTED, action.result
    draft = action.result["draft"]
    assert draft["subject"].startswith("Statement reconciliation: our account at ")
    assert "SPI-9917" in draft["text"] and "which we have not received" in draft["text"]
    assert action.result["sent"] is None  # the sample book never sends mail
    # The cockpit's recent actions carry the draft, for the person to copy into their email.
    cockpit = client.get(f"{_api(tenant)}/books/{book.id}/cockpit", headers=headers)
    assert cockpit.status_code == 200, cockpit.text
    [shown] = [a for a in cockpit.json()["recent"] if a["id"] == str(action.id)]
    assert shown["result"]["draft"]["subject"] == draft["subject"]
