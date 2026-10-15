"""The snapshot reads an invoice's payment state as it is now, not as the last ledger rebuild saw it.

A person approves a payment (the settlement becomes SCHEDULED) and imports the bank statement a few
seconds later. The snapshot read the state from the Promise ledger's copy, which the debounced
ledger rebuild refreshes later, so in between the scheduled invoice looked unpaid and unplanned: the
bank import only suggested the payment it should have matched by itself (spec 81 failed this way),
and a plan made in that window saw an old state. The settlement row is the truth; the snapshot now
takes it whenever it is newer than the ledger's copy.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.models.helm import HelmBook, HelmSettings
from app.services.helm import ledger_store, settlements, snapshot_store
from app.services.helm import vocabulary as v
from tests.security.plans import put_on_plan
from tests.services.helm import factories


def test_a_payment_scheduled_since_the_last_rebuild_is_scheduled_in_the_snapshot(db_session, tenant):
    put_on_plan(db_session, tenant.organization, "business")
    org, ws = tenant.organization.id, tenant.workspace
    book = HelmBook(organization_id=org, workspace_id=ws.id, kind=v.BOOK_WORKSPACE, name="Engineering",
                    status=v.BOOK_ACTIVE)
    db_session.add(book)
    db_session.flush()
    db_session.add(HelmSettings(book_id=book.id, organization_id=org,
                                identities=[{"name": "Meridian Wholesale", "tax_id": None}]))
    item = factories.invoice(db_session, workspace=ws, organization_id=org, number="KP-1182",
                             supplier="Konkan Packaging", amount=50_000, on=date.today() - timedelta(days=20))
    db_session.commit()
    ledger_store.rebuild_workspace(db_session, workspace_id=ws.id)
    db_session.commit()

    run = date.today() + timedelta(days=1)
    settlements.set_state(db_session, organization_id=org, workspace_id=ws.id, work_item_id=item.id,
                          state=v.SETTLEMENT_SCHEDULED, source=v.SOURCE_HELM, run_date=run)
    db_session.commit()  # no ledger rebuild yet: it is debounced

    [payable] = snapshot_store.take(db_session, book).payables
    assert payable.settlement_state == v.SETTLEMENT_SCHEDULED
    assert payable.run_date == run and payable.settlement_source == v.SOURCE_HELM

    # A held invoice released, a payment recorded: the newest state always wins.
    settlements.set_state(db_session, organization_id=org, workspace_id=ws.id, work_item_id=item.id,
                          state=v.SETTLEMENT_PAID, source=v.SOURCE_PERSON, paid_on=date.today())
    db_session.commit()
    [payable] = snapshot_store.take(db_session, book).payables
    assert payable.settlement_state == v.SETTLEMENT_PAID and payable.paid_on == date.today()
