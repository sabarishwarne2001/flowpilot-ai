"""Bank statement import on real Postgres, through the API (owner addition A).

A Business book with a payment Helm scheduled (Konkan Packaging, KP-1182), one it did not (Zenith
Steel, ZEN-77) and a customer invoice (Kesarbagh Retail, S-77). An HDFC-style CSV is imported:

* the scheduled payment named in the narration is matched by Helm, and the customer's payment too;
  each becomes a "Record paid" action the worker runs, so the invoice is PAID with the bank line as
  its evidence (source STATEMENT), certified and undoable;
* the unplanned payment of the right amount is only suggested; bank charges are left for a person;
* the closing balance becomes the account's cash; two accounts in one currency add up;
* the same file twice adds nothing; an overlapping period adds only its new lines;
* a person confirms a suggestion, ignores a fee, and reopens a match (the payment record is undone
  and the invoice is scheduled again); each decision is audited with the person who made it;
* a match a person reopened or undid is never made again by itself on a later import, only suggested;
* reopening right after the worker recorded the payment undoes that record instead of overwriting it;
* undoing a payment record takes back the value held that rested on it;
* a payment record the Guard blocks sends the line back to a person, and the invoice stays free;
* the early-payment price settles an invoice only inside its discount window, and a narration that also
  names another invoice of the book is only a suggestion;
* members, plans without Helm and other organizations are refused.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog, AuditResourceType
from app.models.helm import HelmAction, HelmBankLine, HelmBook, HelmSettlement, HelmValueEvent
from app.services.helm import bank, ledger_store, service, settlements, value
from app.services.helm import vocabulary as v
from tests.engines.conftest import drain
from tests.security.plans import put_on_plan
from tests.services.helm import factories

API = "/api/v1"
M = 1_000_000
TODAY = date.today()


def _url(tenant, path: str) -> str:
    return f"{API}/organizations/{tenant.organization.id}/helm{path}"


def _d(on: date) -> str:
    return on.strftime("%d/%m/%Y")


def _csv(rows: list[tuple[date, str, str, str, str, str]]) -> bytes:
    head = "HDFC BANK Ltd\nAccount No :XXXXXXXX6819\n\nDate,Narration,Chq./Ref.No.,Withdrawal Amt.,Deposit Amt.,Closing Balance\n"
    body = "".join(f'{_d(on)},{text},{ref},"{out}","{inn}","{bal}"\n' for on, text, ref, out, inn, bal in rows)
    return (head + body).encode()


ROWS = [
    (TODAY - timedelta(days=1), "NEFT-KONKAN PACKAGING PVT LTD-KP-1182", "N1", "1,20,000.00", "", "3,80,000.00"),
    (TODAY - timedelta(days=1), "NEFT-9912-TRANSFER", "N2", "50,000.00", "", "3,30,000.00"),
    (TODAY, "UPI-KESARBAGH RETAIL-S-77", "U3", "", "90,000.00", "4,20,000.00"),
    (TODAY, "BANK CHARGES", "C4", "118.00", "", "4,19,882.00"),
]


@pytest.fixture()
def book(client, db_session, tenant, test_sessions):
    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    created = client.post(_url(tenant, "/books"), json={"workspace_id": str(tenant.workspace.id)},
                          headers=tenant.owner.headers)
    assert created.status_code == 201, created.text
    book = created.json()
    response = client.patch(_url(tenant, f"/books/{book['id']}/settings"),
                            json={"identities": [{"name": "Meridian Wholesale", "tax_id": "27AAACM1234F1Z5"}]},
                            headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    org, ws = tenant.organization.id, tenant.workspace
    konkan = factories.invoice(db_session, workspace=ws, organization_id=org, number="KP-1182",
                               supplier="Konkan Packaging Pvt Ltd", amount=120_000, on=TODAY - timedelta(days=20))
    zenith = factories.invoice(db_session, workspace=ws, organization_id=org, number="ZEN-77",
                               supplier="Zenith Steel", amount=50_000, on=TODAY - timedelta(days=12))
    sale = factories.invoice(db_session, workspace=ws, organization_id=org, number="S-77",
                             supplier="Meridian Wholesale", buyer="Kesarbagh Retail", amount=90_000,
                             on=TODAY - timedelta(days=15), extra={"supplier_gstin": "27AAACM1234F1Z5"})
    settlements.set_state(db_session, organization_id=org, workspace_id=ws.id, work_item_id=konkan.id,
                          state=v.SETTLEMENT_SCHEDULED, source=v.SOURCE_HELM, run_date=TODAY - timedelta(days=1))
    db_session.commit()
    drain(only=[v.JOB_LEDGER_BUILD])
    book.update(konkan=konkan.id, zenith=zenith.id, sale=sale.id)
    return book


def _account(client, tenant, book, name="HDFC current", number="50200012346819", headers=None):
    created = client.post(_url(tenant, f"/books/{book['id']}/bank-accounts"),
                          json={"name": name, "number": number, "currency": "INR"},
                          headers=headers or tenant.owner.headers)
    assert created.status_code == 201, created.text
    return created.json()


def _import(client, tenant, book, account, data: bytes, *, name="hdfc-oct.csv", mapping=None, headers=None):
    form = {"account_id": account["id"]}
    if mapping is not None:
        form["mapping"] = json.dumps(mapping)
    return client.post(_url(tenant, f"/books/{book['id']}/bank-statements"), data=form,
                       files={"file": (name, data, "text/csv")}, headers=headers or tenant.owner.headers)


def _lines(db_session, account) -> dict[str, HelmBankLine]:
    db_session.expire_all()
    rows = db_session.execute(select(HelmBankLine).where(
        HelmBankLine.bank_account_id == uuid.UUID(account["id"]))).scalars().all()
    return {r.description: r for r in rows}


def _settlement(db_session, work_item_id) -> HelmSettlement:
    db_session.expire_all()
    return db_session.execute(select(HelmSettlement).where(HelmSettlement.work_item_id == work_item_id)).scalar_one()


def test_an_import_matches_what_the_evidence_settles_and_leaves_the_rest_for_a_person(client, db_session, tenant,
                                                                                       book) -> None:
    account = _account(client, tenant, book)
    assert account["label"] == "HDFC current ····6819" and account["last4"] == "6819"

    preview = client.post(_url(tenant, f"/books/{book['id']}/bank-statements/preview"),
                          data={"account_id": account["id"]}, files={"file": ("hdfc.csv", _csv(ROWS), "text/csv")},
                          headers=tenant.owner.headers)
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert shown["header_row"] == 4 and shown["saved"] is False
    assert (shown["mapping"]["debit"], shown["mapping"]["credit"]) == ("Withdrawal Amt.", "Deposit Amt.")
    assert shown["sample"][0]["amount_micros"] == -120_000 * M

    response = _import(client, tenant, book, account, _csv(ROWS))
    assert response.status_code == 201, response.text
    result = response.json()
    assert (result["lines_read"], result["lines_new"], result["lines_matched"], result["lines_waiting"]) == (4, 4, 2, 2)
    assert result["closing_balance_micros"] == 419_882 * M

    lines = _lines(db_session, account)
    konkan, transfer = lines["NEFT-KONKAN PACKAGING PVT LTD-KP-1182"], lines["NEFT-9912-TRANSFER"]
    customer, charges = lines["UPI-KESARBAGH RETAIL-S-77"], lines["BANK CHARGES"]
    assert (konkan.status, konkan.work_item_id, konkan.match["by"]) == ("MATCHED", book["konkan"], "HELM")
    assert (customer.status, customer.work_item_id) == ("MATCHED", book["sale"])
    assert transfer.status == "SUGGESTED"
    assert [s["number"] for s in transfer.match["suggestions"]] == ["ZEN-77"]
    assert charges.status == "UNMATCHED"

    # The worker records each payment through the executor: guarded, certified, with the line as evidence.
    drain(only=[v.JOB_EXECUTE])
    paid = _settlement(db_session, book["konkan"])
    assert (paid.state, paid.source, paid.paid_on) == ("PAID", "STATEMENT", TODAY - timedelta(days=1))
    assert paid.paid_amount_micros == 120_000 * M
    assert paid.evidence["bank_line_id"] == str(konkan.id) and paid.evidence["matched_by"] == "HELM"
    assert paid.evidence["account"] == "HDFC current ····6819"
    action = db_session.get(HelmAction, konkan.action_id)
    assert action.state == v.ACTION_EXECUTED and action.certificate_id is not None
    assert _settlement(db_session, book["sale"]).state == "PAID"

    # The closing balance is the account's cash, typed by no one, read from the bank's own figures.
    settings = client.get(_url(tenant, f"/books/{book['id']}/settings"), headers=tenant.owner.headers).json()
    [cash] = [c for c in settings["cash"] if c["currency"] == "INR"]
    assert (cash["amount_micros"], cash["source"], cash["as_of"]) == (419_882 * M, "BANK", TODAY.isoformat())


def test_the_same_file_adds_nothing_and_an_overlapping_period_adds_only_its_new_lines(client, db_session, tenant,
                                                                                       book) -> None:
    account = _account(client, tenant, book)
    assert _import(client, tenant, book, account, _csv(ROWS)).status_code == 201
    again = _import(client, tenant, book, account, _csv(ROWS), name="hdfc-oct-copy.csv")
    assert again.status_code == 409, again.text
    assert again.json()["details"]["reason"] == "BANK_FILE_SEEN"
    later = ROWS[2:] + [(TODAY, "NEFT-ZENITH STEEL-ZEN-77", "N5", "50,000.00", "", "3,69,882.00")]
    response = _import(client, tenant, book, account, _csv(later), name="hdfc-week.csv")
    assert response.status_code == 201, response.text
    assert (response.json()["lines_read"], response.json()["lines_new"]) == (3, 1)
    assert len(_lines(db_session, account)) == 5


def test_a_person_confirms_ignores_and_reopens(client, db_session, tenant, book) -> None:
    account = _account(client, tenant, book)
    assert _import(client, tenant, book, account, _csv(ROWS)).status_code == 201
    drain(only=[v.JOB_EXECUTE])
    lines = _lines(db_session, account)
    headers = tenant.owner.headers

    wrong = client.post(_url(tenant, f"/bank-lines/{lines['BANK CHARGES'].id}/match"),
                        json={"work_item_id": str(book["zenith"])}, headers=headers)
    assert wrong.status_code == 422 and wrong.json()["details"]["reason"] == "BANK_AMOUNT"

    confirmed = client.post(_url(tenant, f"/bank-lines/{lines['NEFT-9912-TRANSFER'].id}/match"),
                            json={"work_item_id": str(book["zenith"])}, headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "MATCHED" and confirmed.json()["match"]["by"] == "PERSON"
    ignored = client.post(_url(tenant, f"/bank-lines/{lines['BANK CHARGES'].id}/ignore"),
                          json={"note": "Monthly bank charges"}, headers=headers)
    assert ignored.status_code == 200 and ignored.json()["status"] == "IGNORED"
    drain(only=[v.JOB_EXECUTE])
    zenith = _settlement(db_session, book["zenith"])
    assert (zenith.state, zenith.evidence["matched_by"]) == ("PAID", "PERSON")

    waiting = client.get(_url(tenant, f"/books/{book['id']}/bank-lines?status=WAITING"), headers=headers).json()
    assert waiting["lines"] == [] and waiting["counts"] == {"UNMATCHED": 0, "SUGGESTED": 0, "MATCHED": 3, "IGNORED": 1}

    # Reopening a match undoes its payment record: the invoice is scheduled again, the line waits.
    reopened = client.post(_url(tenant, f"/bank-lines/{lines['NEFT-KONKAN PACKAGING PVT LTD-KP-1182'].id}/reopen"),
                           headers=headers)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "UNMATCHED" and reopened.json()["action"] is None
    assert _settlement(db_session, book["konkan"]).state == "SCHEDULED"


def test_two_accounts_in_one_currency_add_up(client, db_session, tenant, book) -> None:
    first = _account(client, tenant, book)
    second = _account(client, tenant, book, name="ICICI collections", number="000405001234")
    assert _import(client, tenant, book, first, _csv(ROWS)).status_code == 201
    other = [(TODAY, "INTEREST CREDIT", "I1", "", "1,000.00", "60,000.00")]
    assert _import(client, tenant, book, second, _csv(other), name="icici.csv").status_code == 201
    settings = client.get(_url(tenant, f"/books/{book['id']}/settings"), headers=tenant.owner.headers).json()
    [cash] = [c for c in settings["cash"] if c["currency"] == "INR"]
    assert cash["amount_micros"] == (419_882 + 60_000) * M


def test_members_plans_without_helm_and_other_organizations_are_refused(client, db_session, tenant, book) -> None:
    account = _account(client, tenant, book)
    assert _import(client, tenant, book, account, _csv(ROWS), headers=tenant.contributor.headers).status_code == 403
    response = _import(client, tenant, book, account, _csv(ROWS))
    line_id = _lines(db_session, account)["BANK CHARGES"].id
    assert response.status_code == 201
    foreign = client.post(f"{API}/organizations/{tenant.foreign_workspace.organization_id}/helm/bank-lines/{line_id}"
                          "/ignore", json={}, headers=tenant.other_org_member.headers)
    assert foreign.status_code in (403, 404), foreign.text
    put_on_plan(db_session, tenant.organization, "developer")
    db_session.commit()
    refused = _import(client, tenant, book, account, _csv(ROWS[:1]), name="one.csv")
    assert refused.status_code == 402, refused.text


def test_an_unreadable_file_or_mapping_is_refused_in_words(client, db_session, tenant, book) -> None:
    account = _account(client, tenant, book)
    nothing = _import(client, tenant, book, account, b"hello,world\n1,2\n", name="notes.csv")
    assert nothing.status_code == 422 and "No header row" in nothing.json()["message"]
    bad = _import(client, tenant, book, account, _csv(ROWS), mapping={"date": "Date", "debit": "Nope"})
    assert bad.status_code == 422 and "no column called 'Nope'" in bad.json()["message"]


KONKAN = "NEFT-KONKAN PACKAGING PVT LTD-KP-1182"


def _later(rows=()) -> bytes:
    """A later, overlapping statement: the Konkan line again and one new line."""
    return _csv([ROWS[0], *rows, (TODAY, "INTEREST CREDIT", "I9", "", "12.00", "4,19,894.00")])


def _actions_for(db_session, work_item_id) -> list[HelmAction]:
    db_session.expire_all()
    return list(db_session.execute(select(HelmAction).where(HelmAction.work_item_id == work_item_id,
                                                            HelmAction.kind == v.ACTION_MARK_PAID)).scalars())


def test_a_match_a_person_reopened_is_never_made_again_by_itself(client, db_session, tenant, book) -> None:
    account = _account(client, tenant, book)
    assert _import(client, tenant, book, account, _csv(ROWS)).status_code == 201
    drain(only=[v.JOB_EXECUTE])
    line = _lines(db_session, account)[KONKAN]
    assert client.post(_url(tenant, f"/bank-lines/{line.id}/reopen"), headers=tenant.owner.headers).status_code == 200
    assert _settlement(db_session, book["konkan"]).state == "SCHEDULED"

    response = _import(client, tenant, book, account, _later(), name="hdfc-week.csv")
    assert response.status_code == 201, response.text
    again = _lines(db_session, account)[KONKAN]
    assert again.status == "SUGGESTED" and again.action_id is None
    assert [s["number"] for s in again.match["suggestions"]] == ["KP-1182"]
    assert any("took this match back" in r for r in again.match["suggestions"][0]["reasons"])
    drain(only=[v.JOB_EXECUTE])
    assert _settlement(db_session, book["konkan"]).state == "SCHEDULED"
    assert [a.state for a in _actions_for(db_session, book["konkan"])] == [v.ACTION_UNDONE]


def test_a_payment_record_undone_in_the_cockpit_is_never_made_again_by_itself(client, db_session, tenant,
                                                                              book) -> None:
    account = _account(client, tenant, book)
    assert _import(client, tenant, book, account, _csv(ROWS)).status_code == 201
    drain(only=[v.JOB_EXECUTE])
    line = _lines(db_session, account)[KONKAN]
    undone = client.post(_url(tenant, f"/actions/{line.action_id}/undo"), headers=tenant.owner.headers)
    assert undone.status_code == 200, undone.text
    assert _lines(db_session, account)[KONKAN].status == "UNMATCHED"

    assert _import(client, tenant, book, account, _later(), name="hdfc-week.csv").status_code == 201
    assert _lines(db_session, account)[KONKAN].status == "SUGGESTED"
    drain(only=[v.JOB_EXECUTE])
    assert _settlement(db_session, book["konkan"]).state == "SCHEDULED"


def _audits(db_session, resource_type, resource_id) -> list[AuditLog]:
    db_session.expire_all()
    return list(db_session.execute(select(AuditLog).where(AuditLog.resource_type == resource_type,
                                                          AuditLog.resource_id == resource_id)
                                   .order_by(AuditLog.created_at)).scalars())


def test_confirm_ignore_and_reopen_are_audited_with_the_person(client, db_session, tenant, book) -> None:
    account = _account(client, tenant, book)
    assert _import(client, tenant, book, account, _csv(ROWS)).status_code == 201
    lines = _lines(db_session, account)
    headers, owner = tenant.owner.headers, tenant.owner.user.id
    konkan, transfer, charges = lines[KONKAN], lines["NEFT-9912-TRANSFER"], lines["BANK CHARGES"]
    action_id = konkan.action_id

    # Reopened before the worker ran: the approved payment record is cancelled, by this person.
    assert client.post(_url(tenant, f"/bank-lines/{konkan.id}/reopen"), headers=headers).status_code == 200
    assert client.post(_url(tenant, f"/bank-lines/{transfer.id}/match"), json={"work_item_id": str(book["zenith"])},
                       headers=headers).status_code == 200
    assert client.post(_url(tenant, f"/bank-lines/{charges.id}/ignore"), json={"note": "Monthly bank charges"},
                       headers=headers).status_code == 200

    [rejected] = [a for a in _audits(db_session, AuditResourceType.HELM_ACTION, action_id) if a.action == "REJECTED"]
    assert rejected.actor_id == owner
    decisions = {}
    for line in (konkan, transfer, charges):
        [row] = _audits(db_session, AuditResourceType.HELM_BANK, line.id)
        assert row.actor_id == owner
        decisions[line.description] = row.details["decision"]
    assert decisions == {KONKAN: "REOPENED", "NEFT-9912-TRANSFER": "MATCHED", "BANK CHARGES": "IGNORED"}
    [matched] = _audits(db_session, AuditResourceType.HELM_BANK, transfer.id)
    assert matched.details["work_item_id"] == str(book["zenith"]) and matched.details["action_id"]
    assert db_session.get(HelmAction, action_id).state == v.ACTION_REJECTED


def test_reopening_just_after_the_worker_recorded_the_payment_undoes_it(client, db_session, tenant, book) -> None:
    account = _account(client, tenant, book)
    assert _import(client, tenant, book, account, _csv(ROWS)).status_code == 201
    line = _lines(db_session, account)[KONKAN]
    action = db_session.get(HelmAction, line.action_id)
    assert action.state == v.ACTION_APPROVED  # what this request read
    drain(only=[v.JOB_EXECUTE])  # meanwhile the worker records the payment
    who = service.viewer(db_session, organization_id=tenant.organization.id, user_id=tenant.owner.user.id,
                         role="OWNER")
    bank.reopen(db_session, who, line.id)
    db_session.commit()
    assert _settlement(db_session, book["konkan"]).state == "SCHEDULED"
    assert db_session.get(HelmAction, action.id).state == v.ACTION_UNDONE


def test_undoing_a_bank_payment_takes_back_the_value_it_evidenced(client, db_session, tenant, book) -> None:
    account = _account(client, tenant, book)
    assert _import(client, tenant, book, account, _csv(ROWS)).status_code == 201
    drain(only=[v.JOB_EXECUTE])
    line = _lines(db_session, account)[KONKAN]
    action_id = line.action_id
    helm_book = db_session.get(HelmBook, uuid.UUID(book["id"]))
    for category, work_item_id in ((v.VALUE_LATE_FEE_AVOIDED, book["konkan"]),
                                   (v.VALUE_DISCOUNT_CAPTURED, book["konkan"]),
                                   (v.VALUE_LATE_FEE_AVOIDED, book["sale"])):
        assert value._record(db_session, helm_book, category=category, work_item_id=work_item_id,
                             workspace_id=tenant.workspace.id, currency="INR", amount_micros=2_400 * M,
                             occurred_on=TODAY - timedelta(days=1), evidence={"paid_on": "bank"}, action_id=None)
    db_session.commit()

    assert client.post(_url(tenant, f"/bank-lines/{line.id}/reopen"), headers=tenant.owner.headers).status_code == 200
    db_session.expire_all()
    left = db_session.execute(select(HelmValueEvent.work_item_id, HelmValueEvent.category)).all()
    assert left == [(book["sale"], v.VALUE_LATE_FEE_AVOIDED)]
    [undone] = [a for a in _audits(db_session, AuditResourceType.HELM_ACTION, action_id) if a.action == "UNDONE"]
    assert sorted(w["category"] for w in undone.details["value_withdrawn"]) == [v.VALUE_DISCOUNT_CAPTURED,
                                                                                 v.VALUE_LATE_FEE_AVOIDED]


def test_a_blocked_payment_record_sends_the_line_back_to_a_person(client, db_session, tenant, book) -> None:
    account = _account(client, tenant, book)
    assert _import(client, tenant, book, account, _csv(ROWS)).status_code == 201
    action_id = _lines(db_session, account)[KONKAN].action_id
    put_on_plan(db_session, tenant.organization, "developer")  # Helm is no longer on the plan when the worker runs
    db_session.commit()
    drain(only=[v.JOB_EXECUTE])
    assert db_session.get(HelmAction, action_id).state == v.ACTION_BLOCKED

    line = _lines(db_session, account)[KONKAN]
    assert (line.status, line.action_id, line.work_item_id) == ("SUGGESTED", None, None)
    [suggestion] = line.match["suggestions"]
    assert suggestion["work_item_id"] == str(book["konkan"]) and "blocked" in suggestion["reasons"][0]
    waiting = client.get(_url(tenant, f"/books/{book['id']}/bank-lines?status=WAITING"),
                         headers=tenant.owner.headers).json()
    assert str(line.id) in [ln["id"] for ln in waiting["lines"]]

    # Back on a plan with Helm, a person confirms it: the invoice was never taken by the blocked match.
    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    confirmed = client.post(_url(tenant, f"/bank-lines/{line.id}/match"), json={"work_item_id": str(book["konkan"])},
                            headers=tenant.owner.headers)
    assert confirmed.status_code == 200, confirmed.text
    drain(only=[v.JOB_EXECUTE])
    assert _settlement(db_session, book["konkan"]).state == "PAID"


def test_a_blocked_payment_record_a_person_rejects_is_never_made_again_by_itself(client, db_session, tenant,
                                                                                 book) -> None:
    account = _account(client, tenant, book)
    assert _import(client, tenant, book, account, _csv(ROWS)).status_code == 201
    action_id = _lines(db_session, account)[KONKAN].action_id
    put_on_plan(db_session, tenant.organization, "developer")
    db_session.commit()
    drain(only=[v.JOB_EXECUTE])
    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    rejected = client.post(_url(tenant, f"/actions/{action_id}/reject"), json={"reason": "Not this invoice"},
                           headers=tenant.owner.headers)
    assert rejected.status_code == 200, rejected.text
    assert _import(client, tenant, book, account, _later(), name="hdfc-week.csv").status_code == 201
    line = _lines(db_session, account)[KONKAN]
    assert line.status == "SUGGESTED" and line.action_id is None


def test_the_early_payment_price_settles_an_invoice_only_inside_its_window(client, db_session, tenant, book) -> None:
    org, ws = tenant.organization.id, tenant.workspace
    late = factories.invoice(db_session, workspace=ws, organization_id=org, number="LL-901",
                             supplier="Lakshmi Logistics", amount=100_000, on=TODAY - timedelta(days=30),
                             terms="2/10 net 30")
    early = factories.invoice(db_session, workspace=ws, organization_id=org, number="LL-902",
                              supplier="Lakshmi Logistics", amount=100_000, on=TODAY - timedelta(days=5),
                              terms="2/10 net 30")
    ledger_store.rebuild_workspace(db_session, workspace_id=ws.id)
    db_session.commit()
    account = _account(client, tenant, book)
    rows = [(TODAY - timedelta(days=1), "NEFT-LAKSHMI LOGISTICS-LL-901", "L1", "98,000.00", "", "3,02,000.00"),
            (TODAY, "NEFT-LAKSHMI LOGISTICS-LL-902", "L2", "98,000.00", "", "2,04,000.00")]
    assert _import(client, tenant, book, account, _csv(rows)).status_code == 201
    lines = _lines(db_session, account)
    after = client.post(_url(tenant, f"/bank-lines/{lines['NEFT-LAKSHMI LOGISTICS-LL-901'].id}/match"),
                        json={"work_item_id": str(late.id)}, headers=tenant.owner.headers)
    assert after.status_code == 422 and after.json()["details"]["reason"] == "BANK_AMOUNT", after.text
    inside = client.post(_url(tenant, f"/bank-lines/{lines['NEFT-LAKSHMI LOGISTICS-LL-902'].id}/match"),
                         json={"work_item_id": str(early.id)}, headers=tenant.owner.headers)
    assert inside.status_code == 200, inside.text


def test_a_narration_naming_another_invoice_of_the_book_is_only_a_suggestion(client, db_session, tenant,
                                                                              book) -> None:
    account = _account(client, tenant, book)
    rows = [(TODAY - timedelta(days=1), "NEFT-KONKAN PACKAGING PVT LTD-KP-1182 ZEN-77", "N1", "1,20,000.00", "",
             "3,80,000.00")]
    assert _import(client, tenant, book, account, _csv(rows)).status_code == 201
    line = _lines(db_session, account)["NEFT-KONKAN PACKAGING PVT LTD-KP-1182 ZEN-77"]
    assert line.status == "SUGGESTED" and line.action_id is None
    assert any("ZEN-77" in r for r in line.match["suggestions"][0]["reasons"])
