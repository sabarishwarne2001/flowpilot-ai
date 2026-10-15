"""The Promise ledger is a rebuildable projection (brief §4.2): the incremental updates a document's
life triggers and the nightly full rebuild end with exactly the same rows."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import delete, select

from app.models.helm import HelmBook, HelmPromise, HelmSettings
from app.models.job import Job
from app.services.helm import ledger_store
from app.services.helm import vocabulary as v
from tests.security.plans import put_on_plan
from tests.services.helm import factories


def _rows(db):
    return sorted((p.source_key, p.content_hash, str(p.id)) for p in db.execute(select(HelmPromise)).scalars())


def test_incremental_updates_and_the_nightly_rebuild_agree(client, db_session, tenant, test_sessions):
    put_on_plan(db_session, tenant.organization, "business")
    book = HelmBook(organization_id=tenant.organization.id, workspace_id=tenant.workspace.id, kind=v.BOOK_WORKSPACE,
                    name="Engineering", status=v.BOOK_ACTIVE)
    db_session.add(book)
    db_session.flush()
    db_session.add(HelmSettings(book_id=book.id, organization_id=book.organization_id,
                                identities=[{"name": "Meridian Wholesale", "tax_id": "27AAACM1234F1Z5"}]))
    db_session.commit()
    today = date.today()
    org, ws = tenant.organization.id, tenant.workspace

    from app.workers.handlers.helm import handle_ledger_build

    def incremental():
        # The job body a document's change enqueues (debounced in production; run at once here).
        handle_ledger_build({"workspace_id": str(ws.id), "organization_id": str(org)})

    def change(make):
        item = make()
        db_session.commit()
        incremental()
        return item

    change(lambda: factories.invoice(db_session, workspace=ws, organization_id=org, number="A-1", supplier="Alpha",
                                     amount=1000, on=today - timedelta(days=40), terms="2/10 net 30"))
    second = change(lambda: factories.invoice(db_session, workspace=ws, organization_id=org, number="B-1",
                                              supplier="Beta", amount=2000, on=today - timedelta(days=5)))
    change(lambda: factories.invoice(db_session, workspace=ws, organization_id=org, number="S-1",
                                     supplier="Meridian Wholesale", buyer="Kesarbagh Retail", amount=5000,
                                     on=today - timedelta(days=3), extra={"supplier_gstin": "27AAACM1234F1Z5"}))
    change(lambda: factories.document(db_session, workspace=ws, organization_id=org, role="STATEMENT",
                                      entities={"closing_balance": "100000", "statement_date": today.isoformat(),
                                                "transactions": [{"date": today.isoformat(), "description": "NEFT BETA",
                                                                  "debit": "2000.00"}]}, on=today))
    # A credit note, then a deletion: both reach other documents' promises.
    change(lambda: factories.document(db_session, workspace=ws, organization_id=org, role="CREDIT_NOTE",
                                      entities={"vendor_name": "Alpha", "invoice_reference": "A-1"}, number="CN-1",
                                      on=today, total=100, vendor="Alpha"))
    from app import crud

    crud.delete_work_item(db_session, db_obj=db_session.get(type(second), second.id))
    db_session.commit()
    incremental()

    db_session.expire_all()
    incremental = _rows(db_session)
    assert incremental, "the incremental updates wrote nothing"
    kinds = {p.kind for p in db_session.execute(select(HelmPromise)).scalars()}
    assert {v.KIND_PAYMENT_DUE, v.KIND_PAYMENT_EXPECTED, v.KIND_DISCOUNT_OFFERED} <= kinds

    # The nightly rebuild from nothing writes the same rows (ids differ: they are new rows) ...
    db_session.execute(delete(HelmPromise))
    db_session.commit()
    assert handle_ledger_build({"full": True})["rebuilt"] >= 1
    db_session.expire_all()
    rebuilt = _rows(db_session)
    assert [(k, h) for k, h, _ in rebuilt] == [(k, h) for k, h, _ in incremental]

    # ... and a nightly rebuild over an up-to-date ledger changes nothing at all, ids included.
    handle_ledger_build({"full": True})
    db_session.expire_all()
    assert _rows(db_session) == rebuilt
    result = ledger_store.rebuild_workspace(db_session, workspace_id=ws.id)
    assert (result.inserted, result.updated, result.deleted) == (0, 0, 0)


def test_a_burst_of_documents_rebuilds_the_ledger_once(client, db_session, tenant, test_sessions):
    put_on_plan(db_session, tenant.organization, "business")
    book = HelmBook(organization_id=tenant.organization.id, workspace_id=tenant.workspace.id, kind=v.BOOK_WORKSPACE,
                    name="Engineering", status=v.BOOK_ACTIVE)
    db_session.add(book)
    db_session.commit()
    for n in range(5):
        ledger_store.enqueue_rebuild(db_session, workspace_id=tenant.workspace.id,
                                     organization_id=tenant.organization.id, reason=f"doc {n}")
    db_session.commit()
    jobs = db_session.execute(select(Job).where(Job.job_type == v.JOB_LEDGER_BUILD)).scalars().all()
    assert len(jobs) <= 2  # one per ten-second bucket


def test_a_workspace_no_book_reads_is_never_rebuilt(client, db_session, tenant, test_sessions):
    assert ledger_store.enqueue_rebuild(db_session, workspace_id=tenant.workspace.id,
                                        organization_id=tenant.organization.id, reason="no book") is False
