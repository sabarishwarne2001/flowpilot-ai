"""Bank statement lines and workspace access, on real Postgres through the API (owner addition A).

An Enterprise organization with two workspaces: Engineering and Finance. A Billing person is granted
Engineering only. The rest of Helm masks what lives in a workspace a person cannot read, and so must
the bank pages:

* a workspace book for Finance: its bank accounts, imports and lines are refused (HELM_NO_ACCESS);
* the organization book: a line matched to a Finance invoice shows neither the match nor the narration,
  reference or balance, and a hold warning about a Finance invoice is not shown either;
* the Billing person's own import never records a payment on a Finance invoice: it is only suggested;
* they cannot reopen a Finance match (which would undo its payment record), nor ignore a line whose
  suggestion is a Finance invoice, and the executor refuses such an undo too;
* deleting a matched invoice keeps the line's workspace, so the line stays masked.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from app.models.helm import HelmAction, HelmBankLine
from app.models.organization import OrganizationRole
from app.models.work_item import WorkItem
from app.models.workspace import Workspace, WorkspaceRole, WorkspaceStatus
from app.services.helm import executor, settlements
from app.services.helm import vocabulary as v
from app.services.helm.guard import Actor
from tests.conftest import _grant, _make_user, _seat
from tests.engines.conftest import drain
from tests.engines.test_helm_bank_import_live import TODAY, _account, _csv, _import, _lines, _settlement, _url
from tests.security.plans import put_on_plan
from tests.services.helm import factories

HOLD_REASON = "The supplier's bank account changed on its last invoice"

ROWS = [
    (TODAY - timedelta(days=1), "NEFT-KONKAN PACKAGING PVT LTD-KP-1182", "N1", "1,20,000.00", "", "3,80,000.00"),
    (TODAY - timedelta(days=1), "NEFT-9912-TRANSFER", "N2", "50,000.00", "", "3,30,000.00"),
    (TODAY, "NEFT-RAJ METALS-RM-301", "N3", "75,000.00", "", "2,55,000.00"),
]


@pytest.fixture()
def org(client, db_session, tenant, test_sessions):
    """The organization book, a book for Finance, three Finance invoices and a Billing person who
    can read Engineering only."""
    put_on_plan(db_session, tenant.organization, "enterprise")
    finance = Workspace(organization_id=tenant.organization.id, slug="finance", workspace_name="Finance",
                        status=WorkspaceStatus.ACTIVE)
    db_session.add(finance)
    db_session.flush()
    billing = _make_user(db_session, f"billing-{uuid.uuid4().hex[:8]}@acme.com")
    _seat(db_session, tenant.organization, billing, OrganizationRole.BILLING)
    _grant(db_session, tenant.workspace, billing, WorkspaceRole.VIEWER)
    db_session.commit()
    books = {}
    for name, body in (("org", {"kind": "ORGANIZATION"}), ("finance", {"workspace_id": str(finance.id)})):
        created = client.post(_url(tenant, "/books"), json=body, headers=tenant.owner.headers)
        assert created.status_code == 201, created.text
        books[name] = created.json()
        response = client.patch(_url(tenant, f"/books/{books[name]['id']}/settings"),
                                json={"identities": [{"name": "Meridian Wholesale", "tax_id": "27AAACM1234F1Z5"}]},
                                headers=tenant.owner.headers)
        assert response.status_code == 200, response.text
    oid = tenant.organization.id
    konkan = factories.invoice(db_session, workspace=finance, organization_id=oid, number="KP-1182",
                               supplier="Konkan Packaging Pvt Ltd", amount=120_000, on=TODAY - timedelta(days=20))
    zenith = factories.invoice(db_session, workspace=finance, organization_id=oid, number="ZEN-77",
                               supplier="Zenith Steel", amount=50_000, on=TODAY - timedelta(days=12))
    raj = factories.invoice(db_session, workspace=finance, organization_id=oid, number="RM-301",
                            supplier="Raj Metals", amount=75_000, on=TODAY - timedelta(days=10))
    settlements.set_state(db_session, organization_id=oid, workspace_id=finance.id, work_item_id=konkan.id,
                          state=v.SETTLEMENT_SCHEDULED, source=v.SOURCE_HELM, run_date=TODAY - timedelta(days=1))
    settlements.set_state(db_session, organization_id=oid, workspace_id=finance.id, work_item_id=raj.id,
                          state=v.SETTLEMENT_HELD, source=v.SOURCE_HELM, hold_reason=HOLD_REASON)
    db_session.commit()
    drain(only=[v.JOB_LEDGER_BUILD])
    return {"book": books["org"], "finance_book": books["finance"], "finance": finance.id, "billing": billing,
            "konkan": konkan.id, "zenith": zenith.id, "raj": raj.id}


def _reason(response) -> str:
    return response.json()["details"]["reason"]


def test_a_workspace_book_the_person_cannot_read_is_refused(client, db_session, tenant, org) -> None:
    book = org["finance_book"]
    account = _account(client, tenant, book)
    assert _import(client, tenant, book, account, _csv(ROWS)).status_code == 201
    headers = org["billing"].headers
    for path in ("bank-accounts", "bank-imports", "bank-lines"):
        response = client.get(_url(tenant, f"/books/{book['id']}/{path}"), headers=headers)
        assert response.status_code == 403, (path, response.text)
        assert _reason(response) == "HELM_NO_ACCESS"
    # The owner reads them.
    assert client.get(_url(tenant, f"/books/{book['id']}/bank-lines"), headers=tenant.owner.headers).status_code == 200


def test_a_match_in_an_unreadable_workspace_is_masked(client, db_session, tenant, org) -> None:
    book = org["book"]
    account = _account(client, tenant, book)
    assert _import(client, tenant, book, account, _csv(ROWS)).status_code == 201
    lines = _lines(db_session, account)
    konkan = lines["NEFT-KONKAN PACKAGING PVT LTD-KP-1182"]
    assert konkan.status == "MATCHED"

    shown = client.get(_url(tenant, f"/books/{book['id']}/bank-lines"), headers=org["billing"].headers)
    assert shown.status_code == 200, shown.text
    by_id = {line["id"]: line for line in shown.json()["lines"]}
    matched = by_id[str(konkan.id)]
    assert matched["readable"] is False and matched["work_item_id"] is None
    assert (matched["description"], matched["reference"], matched["balance_micros"]) == ("hidden", "hidden", None)
    assert "KP-1182" not in json.dumps(matched)
    mine = {line["id"]: line for line in client.get(_url(tenant, f"/books/{book['id']}/bank-lines"),
                                                     headers=tenant.owner.headers).json()["lines"]}
    assert mine[str(konkan.id)]["description"] == "NEFT-KONKAN PACKAGING PVT LTD-KP-1182"


def test_a_hold_warning_about_an_unreadable_invoice_is_masked(client, db_session, tenant, org) -> None:
    book = org["book"]
    account = _account(client, tenant, book)
    assert _import(client, tenant, book, account, _csv(ROWS)).status_code == 201
    raj = _lines(db_session, account)["NEFT-RAJ METALS-RM-301"]
    assert raj.status == "SUGGESTED" and HOLD_REASON in raj.match["warning"]

    shown = client.get(_url(tenant, f"/books/{book['id']}/bank-lines"), headers=org["billing"].headers).json()
    [held] = [line for line in shown["lines"] if line["id"] == str(raj.id)]
    assert held["match"]["suggestions"][0]["readable"] is False
    assert HOLD_REASON not in json.dumps(held) and "RM-301" not in json.dumps(held["match"])
    assert held["match"]["warning"] == "A suggested invoice in a workspace you cannot read was on hold."
    mine = client.get(_url(tenant, f"/books/{book['id']}/bank-lines"), headers=tenant.owner.headers).json()
    assert HOLD_REASON in [line for line in mine["lines"] if line["id"] == str(raj.id)][0]["match"]["warning"]


def test_an_import_never_records_a_payment_in_a_workspace_the_importer_cannot_read(client, db_session, tenant,
                                                                                    org) -> None:
    book = org["book"]
    account = _account(client, tenant, book, headers=org["billing"].headers)
    response = _import(client, tenant, book, account, _csv(ROWS), headers=org["billing"].headers)
    assert response.status_code == 201, response.text
    assert response.json()["lines_matched"] == 0
    konkan = _lines(db_session, account)["NEFT-KONKAN PACKAGING PVT LTD-KP-1182"]
    assert konkan.status == "SUGGESTED" and konkan.action_id is None
    assert [s["number"] for s in konkan.match["suggestions"]] == ["KP-1182"]
    assert db_session.execute(select(HelmAction).where(HelmAction.work_item_id == org["konkan"])).first() is None
    drain(only=[v.JOB_EXECUTE])
    assert _settlement(db_session, org["konkan"]).state == "SCHEDULED"


def _matched(client, db_session, tenant, org):
    account = _account(client, tenant, org["book"])
    assert _import(client, tenant, org["book"], account, _csv(ROWS)).status_code == 201
    drain(only=[v.JOB_EXECUTE])
    lines = _lines(db_session, account)
    assert _settlement(db_session, org["konkan"]).state == "PAID"
    return account, lines


def test_a_person_cannot_reopen_a_match_in_an_unreadable_workspace(client, db_session, tenant, org) -> None:
    _, lines = _matched(client, db_session, tenant, org)
    konkan = lines["NEFT-KONKAN PACKAGING PVT LTD-KP-1182"]
    reopen = client.post(_url(tenant, f"/bank-lines/{konkan.id}/reopen"), headers=org["billing"].headers)
    assert reopen.status_code == 403 and _reason(reopen) == "HELM_NO_ACCESS", reopen.text
    assert _settlement(db_session, org["konkan"]).state == "PAID"


def test_a_person_cannot_ignore_a_line_suggested_to_an_unreadable_invoice(client, db_session, tenant, org) -> None:
    account, lines = _matched(client, db_session, tenant, org)
    transfer = lines["NEFT-9912-TRANSFER"]
    assert transfer.status == "SUGGESTED"
    ignore = client.post(_url(tenant, f"/bank-lines/{transfer.id}/ignore"), json={"note": "no"},
                         headers=org["billing"].headers)
    assert ignore.status_code == 403 and _reason(ignore) == "HELM_NO_ACCESS", ignore.text
    assert _lines(db_session, account)["NEFT-9912-TRANSFER"].status == "SUGGESTED"


def test_the_executor_refuses_an_undo_by_someone_who_cannot_read_the_workspace(client, db_session, tenant,
                                                                                org) -> None:
    _, lines = _matched(client, db_session, tenant, org)
    action = db_session.get(HelmAction, lines["NEFT-KONKAN PACKAGING PVT LTD-KP-1182"].action_id)
    with pytest.raises(executor.ActionError) as refused:
        executor.undo(db_session, action=action, actor=Actor(user_id=str(org["billing"].user.id), role="BILLING",
                                                             can_read_workspace=False))
    assert refused.value.code == "HELM_NO_ACCESS" and refused.value.status_code == 403
    db_session.rollback()
    assert _settlement(db_session, org["konkan"]).state == "PAID"


def test_deleting_a_matched_invoice_keeps_the_line_masked(client, db_session, tenant, org) -> None:
    book = org["book"]
    account = _account(client, tenant, book)
    assert _import(client, tenant, book, account, _csv(ROWS)).status_code == 201
    konkan = _lines(db_session, account)["NEFT-KONKAN PACKAGING PVT LTD-KP-1182"]
    db_session.execute(delete(WorkItem).where(WorkItem.id == org["konkan"]))
    db_session.commit()
    line = db_session.get(HelmBankLine, konkan.id)
    db_session.refresh(line)
    assert (line.work_item_id, line.workspace_id) == (None, org["finance"])
    shown = client.get(_url(tenant, f"/books/{book['id']}/bank-lines"), headers=org["billing"].headers).json()
    [masked] = [ln for ln in shown["lines"] if ln["id"] == str(konkan.id)]
    assert masked["readable"] is False and "KP-1182" not in json.dumps(masked)
