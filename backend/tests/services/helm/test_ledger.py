"""The Promise ledger's rules (brief §4), as a pure function of the facts."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.core import normalize as nz
from app.services.helm import identity as ident
from app.services.helm import ledger
from app.services.helm import vocabulary as v

pytestmark = pytest.mark.no_db

M = 1_000_000
ORG = uuid.UUID(int=1)
WS = uuid.UUID(int=2)
AS_OF = date(2026, 10, 15)
US = ident.Identities.of([{"name": "Meridian Wholesale Distributors Pvt Ltd", "tax_id": "27AAACM1234F1Z5"}])


def doc(n: int, role: str, entities: dict, *, number=None, on=None, total=None, currency="INR", vendor=None,
        tax=None, confidence=v.CONFIDENCE_EXTRACTED, filename=None, po=None, text="") -> ledger.DocumentFacts:
    vendor_key = None
    if vendor or tax:
        vendor_key = nz.vendor_key(vendor, tax)
    return ledger.DocumentFacts(
        work_item_id=uuid.UUID(int=100 + n), workspace_id=WS, organization_id=ORG,
        filename=filename or f"{role.lower()}-{n}.pdf", role=role, received_on=on or AS_OF, entities=entities,
        vendor_key=vendor_key, document_number=number, document_date=on, currency=currency,
        total_micros=total * M if total is not None else None, po_reference=po, confidence=confidence, text_head=text)


def build(*docs, **kw):
    return ledger.build(ledger.LedgerInputs(as_of=AS_OF, documents=list(docs), identities=kw.pop("identities", US),
                                            **kw))


def of_kind(rows, kind):
    return [r for r in rows if r.kind == kind]


def test_direction_comes_from_our_identity():
    purchase = doc(1, "INVOICE", {"vendor_name": "Deccan Packaging", "bill_to": "Meridian Wholesale Distributors",
                                  "payment_terms": "Net 30", "invoice_date": "2026-10-01"},
                   number="DP-1", on=date(2026, 10, 1), total=118_000, vendor="Deccan Packaging")
    sale = doc(2, "INVOICE", {"vendor_name": "Meridian Wholesale Distributors Pvt Ltd", "seller_gstin": "27AAACM1234F1Z5",
                              "bill_to": "Kesarbagh Retail", "payment_terms": "Net 15"},
               number="S-1", on=date(2026, 10, 1), total=90_000, vendor="Meridian Wholesale Distributors Pvt Ltd",
               tax="27AAACM1234F1Z5")
    rows = build(purchase, sale)
    payable = of_kind(rows, v.KIND_PAYMENT_DUE)
    receivable = of_kind(rows, v.KIND_PAYMENT_EXPECTED)
    assert [r.counterparty_name for r in payable] == ["Deccan Packaging"]
    assert [r.counterparty_name for r in receivable] == ["Kesarbagh Retail"]
    assert payable[0].due_date == date(2026, 10, 31) and payable[0].direction == v.DIRECTION_WE_OWE
    assert receivable[0].due_date == date(2026, 10, 16) and receivable[0].direction == v.DIRECTION_OWED_TO_US
    assert receivable[0].status == v.STATUS_OPEN


def test_without_identities_every_invoice_is_a_payable():
    sale = doc(2, "INVOICE", {"vendor_name": "Meridian Wholesale Distributors Pvt Ltd"}, number="S-1",
               on=date(2026, 10, 1), total=90_000, vendor="Meridian Wholesale Distributors Pvt Ltd")
    rows = build(sale, identities=ident.Identities(frozenset(), frozenset()))
    assert [r.kind for r in rows] == [v.KIND_PAYMENT_DUE]


def test_a_stated_due_date_beats_the_terms_and_unreadable_terms_assume_nothing():
    stated = doc(1, "INVOICE", {"vendor_name": "A", "due_date": "2026-10-20", "payment_terms": "Net 60"},
                 number="A-1", on=date(2026, 10, 1), total=100, vendor="A")
    vague = doc(2, "INVOICE", {"vendor_name": "B", "payment_terms": "as agreed"}, number="B-1",
                on=date(2026, 10, 1), total=100, vendor="B")
    rows = {r.counterparty_name: r for r in of_kind(build(stated, vague), v.KIND_PAYMENT_DUE)}
    assert rows["A"].due_date == date(2026, 10, 20) and rows["A"].details["due_basis"] == "stated on the invoice"
    assert rows["B"].due_date == date(2026, 10, 1)
    assert "no due date or readable terms" in rows["B"].details["due_basis"]
    assert not of_kind(build(vague), v.KIND_DISCOUNT_OFFERED)


def test_an_overdue_unpaid_payable_is_broken_and_a_paid_one_kept():
    late = doc(1, "INVOICE", {"vendor_name": "A", "payment_terms": "Net 10"}, number="A-1", on=date(2026, 9, 1),
               total=100, vendor="A")
    paid = doc(2, "INVOICE", {"vendor_name": "B", "payment_terms": "Net 10"}, number="B-1", on=date(2026, 9, 1),
               total=100, vendor="B")
    settlements = {paid.work_item_id: ledger.SettlementFacts(work_item_id=paid.work_item_id, state=v.SETTLEMENT_PAID,
                                                            source=v.SOURCE_PERSON, paid_on=date(2026, 9, 10))}
    rows = {r.counterparty_name: r for r in of_kind(build(late, paid, settlements=settlements), v.KIND_PAYMENT_DUE)}
    assert rows["A"].status == v.STATUS_BROKEN and rows["A"].details["days_late"] == 34
    assert rows["B"].status == v.STATUS_KEPT
    assert rows["B"].fulfilments[0]["kind"] == "PAYMENT" and rows["B"].fulfilments[0]["source"] == v.SOURCE_PERSON


def test_a_statement_line_settles_an_invoice_by_amount_date_and_payee():
    invoice = doc(1, "INVOICE", {"vendor_name": "Deccan Packaging Industries", "payment_terms": "Net 30"},
                  number="DP-77", on=date(2026, 9, 1), total=118_000, vendor="Deccan Packaging Industries")
    other = doc(2, "INVOICE", {"vendor_name": "Shree Ganesh Polymers", "payment_terms": "Net 30"},
                number="SG-5", on=date(2026, 9, 1), total=118_000, vendor="Shree Ganesh Polymers")
    statement = doc(3, "STATEMENT", {"closing_balance": "1250000.00", "statement_date": "2026-09-30",
                                     "transactions": [
                                         {"date": "2026-09-20", "description": "NEFT DECCAN PACKAGING",
                                          "debit": "118000.00"},
                                         {"date": "2026-08-20", "description": "NEFT SHREE GANESH", "debit": "118000.00"},
                                     ]}, on=date(2026, 9, 30))
    rows = {r.counterparty_name: r for r in of_kind(build(invoice, other, statement), v.KIND_PAYMENT_DUE)}
    deccan = rows["Deccan Packaging Industries"]
    assert deccan.details["settlement"]["state"] == v.SETTLEMENT_PAID
    assert deccan.details["settlement"]["source"] == v.SOURCE_STATEMENT
    assert deccan.fulfilments[0]["statement"]["description"] == "NEFT DECCAN PACKAGING"
    # Same amount, but the line predates the invoice: not a payment of it.
    assert rows["Shree Ganesh Polymers"].details["settlement"]["state"] == v.SETTLEMENT_UNPAID
    assert ledger.statement_balance(statement, None) == ("INR", 1_250_000 * M, date(2026, 9, 30))


def test_a_credit_note_reduces_what_is_owed():
    invoice = doc(1, "INVOICE", {"vendor_name": "A", "payment_terms": "Net 30"}, number="A-9", on=date(2026, 10, 1),
                  total=10_000, vendor="A")
    credit = doc(2, "CREDIT_NOTE", {"vendor_name": "A", "invoice_reference": "A-9"}, number="CN-1",
                 on=date(2026, 10, 5), total=1_500, vendor="A")
    row = of_kind(build(invoice, credit), v.KIND_PAYMENT_DUE)[0]
    assert row.amount_micros == 8_500 * M and row.details["credited_micros"] == 1_500 * M
    assert row.status == v.STATUS_PARTIAL and row.fulfilments[0]["kind"] == "CREDIT"


def test_the_terms_discount_is_an_option_until_its_window_closes():
    open_offer = doc(1, "INVOICE", {"vendor_name": "A", "payment_terms": "2/10 net 30"}, number="A-1",
                     on=date(2026, 10, 10), total=100_000, vendor="A")
    missed = doc(2, "INVOICE", {"vendor_name": "B", "payment_terms": "2/10 net 30"}, number="B-1",
                 on=date(2026, 9, 1), total=100_000, vendor="B")
    rows = {r.counterparty_name: r for r in of_kind(build(open_offer, missed), v.KIND_DISCOUNT_OFFERED)}
    assert rows["A"].status == v.STATUS_OPEN and rows["A"].amount_micros == 2_000 * M
    assert rows["A"].due_date == date(2026, 10, 20) and rows["A"].window_end == date(2026, 11, 9)
    assert rows["B"].status == v.STATUS_EXPIRED


def test_msme_deadlines_use_acceptance_and_the_45_day_rule():
    udyam = "UDYAM-MH-26-0012345"
    short = doc(1, "INVOICE", {"vendor_name": "A", "payment_terms": "Net 30", "udyam_number": udyam}, number="A-1",
                on=date(2026, 10, 1), total=100, vendor="A")
    long = doc(2, "INVOICE", {"vendor_name": "B", "payment_terms": "Net 60"}, number="B-1", on=date(2026, 10, 1),
               total=100, vendor="B", text=f"Udyam Registration No: {udyam.lower()}")
    plain = doc(3, "INVOICE", {"vendor_name": "C", "payment_terms": "Net 60"}, number="C-1", on=date(2026, 10, 1),
                total=100, vendor="C")
    rows = {r.counterparty_name: r for r in of_kind(build(short, long, plain), v.KIND_PAYMENT_DUE)}
    assert rows["A"].details["msme_deadline"]["due"] == "2026-10-31"
    assert rows["A"].details["msme_deadline"]["agreed"] is True
    assert rows["B"].details["udyam_number"] == udyam
    assert rows["B"].details["msme_deadline"]["due"] == "2026-10-16"  # 60 > 45: the law's 15 days
    assert "exceed the 45-day limit" in rows["B"].details["msme_deadline"]["explanation"]
    assert rows["C"].details["msme_deadline"] is None


def test_agreed_prices_come_from_matching_and_break_on_an_overcharge():
    po = doc(1, "PURCHASE_ORDER", {"vendor_name": "A", "po_number": "PO-1"}, number="PO-1", on=date(2026, 9, 1),
             total=1000, vendor="A")
    inv = doc(2, "INVOICE", {"vendor_name": "A", "po_number": "PO-1"}, number="A-1", on=date(2026, 9, 10), total=1100,
              vendor="A", po="PO-1")
    line = ledger.MatchLineFacts(case_id=uuid.UUID(int=9), workspace_id=WS, po_work_item_id=po.work_item_id,
                                 invoice_work_item_id=inv.work_item_id, receipt_work_item_id=None, line_number=0,
                                 description="Corrugated cartons", po_line_index=0, po_unit_price_micros=10 * M,
                                 invoice_unit_price_micros=11 * M, po_quantity=None, receipt_quantity=None,
                                 invoice_quantity=__import__("decimal").Decimal(100), outcome="PRICE_VARIANCE")
    price = of_kind(build(po, inv, match_lines=[line]), v.KIND_PRICE_AGREED)[0]
    assert price.status == v.STATUS_BROKEN and price.amount_micros == 10 * M
    assert price.fulfilments[0]["overcharge_micros"] == 100 * M


def test_deliveries_are_kept_by_goods_receipts_on_time():
    po = doc(1, "PURCHASE_ORDER", {"vendor_name": "A", "po_number": "PO-7", "delivery_date": "2026-09-20"},
             number="PO-7", on=date(2026, 9, 1), total=1000, vendor="A")
    grn = doc(2, "GOODS_RECEIPT", {"vendor_name": "A", "po_number": "PO-7"}, number="GRN-1", on=date(2026, 9, 25),
              po="PO-7")
    late = of_kind(build(po, grn), v.KIND_DELIVERY_DUE)[0]
    assert late.status == v.STATUS_BROKEN  # received on the 25th against the 20th
    missing = of_kind(build(po), v.KIND_DELIVERY_DUE)[0]
    assert missing.status == v.STATUS_BROKEN and not missing.fulfilments


def test_notices_and_compliance_documents_from_obligations():
    contract = doc(1, "CONTRACT", {"vendor_name": "Coastal Logistics"}, filename="coastal-logistics-agreement.pdf")
    cert = doc(2, "OTHER", {"vendor_name": "Deccan Packaging"}, filename="deccan-fire-insurance-policy-2025.pdf")
    renewed = doc(3, "OTHER", {"vendor_name": "Deccan Packaging"}, filename="deccan-fire-insurance-policy-2026.pdf")
    obligations = [
        ledger.ObligationFacts(obligation_id=uuid.UUID(int=500), workspace_id=WS, work_item_id=contract.work_item_id,
                               kind="NOTICE", title="Notice of non-renewal", state="OPEN", due_date=date(2026, 11, 1),
                               done_on=None, counterparty_name="Coastal Logistics", amount_micros=None, currency=None,
                               quote="90 days' written notice", confidence=v.CONFIDENCE_CALIBRATED),
        ledger.ObligationFacts(obligation_id=uuid.UUID(int=501), workspace_id=WS, work_item_id=cert.work_item_id,
                               kind="EXPIRY", title="Fire insurance policy expires", state="OPEN",
                               due_date=date(2026, 10, 10), done_on=None, counterparty_name="Deccan Packaging",
                               amount_micros=None, currency=None, quote=None, confidence=v.CONFIDENCE_CALIBRATED),
        ledger.ObligationFacts(obligation_id=uuid.UUID(int=502), workspace_id=WS, work_item_id=renewed.work_item_id,
                               kind="EXPIRY", title="Fire insurance policy expires", state="OPEN",
                               due_date=date(2027, 10, 10), done_on=None, counterparty_name="Deccan Packaging",
                               amount_micros=None, currency=None, quote=None, confidence=v.CONFIDENCE_CALIBRATED),
    ]
    rows = build(contract, cert, renewed, obligations=obligations)
    notice = of_kind(rows, v.KIND_NOTICE_DEADLINE)[0]
    assert notice.status == v.STATUS_OPEN and notice.direction == v.DIRECTION_OUR_DECISION
    valid = {r.due_date: r for r in of_kind(rows, v.KIND_DOCUMENT_VALID_UNTIL)}
    assert valid[date(2026, 10, 10)].status == v.STATUS_KEPT  # a newer valid document arrived
    assert valid[date(2027, 10, 10)].status == v.STATUS_OPEN


def test_build_is_deterministic_and_keys_are_stable():
    invoice = doc(1, "INVOICE", {"vendor_name": "A", "payment_terms": "2/10 net 30"}, number="A-1",
                  on=date(2026, 10, 10), total=100_000, vendor="A")
    first = build(invoice)
    second = build(invoice)
    assert [r.source_key for r in first] == [r.source_key for r in second]
    assert [r.content_hash for r in first] == [r.content_hash for r in second]
    changed = build(doc(1, "INVOICE", {"vendor_name": "A", "payment_terms": "2/10 net 30"}, number="A-1",
                        on=date(2026, 10, 10), total=100_001, vendor="A"))
    assert {r.source_key for r in changed} == {r.source_key for r in first}
    assert {r.content_hash for r in changed} != {r.content_hash for r in first}


def test_bank_accounts_are_only_ever_masked():
    invoice = doc(1, "INVOICE", {"vendor_name": "A", "vendor_bank_account": "GB29 NWBK 6016 1331 9268 19"},
                  number="A-1", on=date(2026, 10, 1), total=100, vendor="A")
    row = of_kind(build(invoice), v.KIND_PAYMENT_DUE)[0]
    assert row.details["bank_account"] == "•••• 6819"
    assert "NWBK" not in ledger.canonical(row.content())
