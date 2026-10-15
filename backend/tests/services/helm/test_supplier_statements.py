"""A supplier's statement of account is not a bank statement (owner addition B, and a defect it found).

Both carry the STATEMENT role (the role classifier fires on "statement of account" either way). A
supplier's statement lists the invoices they billed us, with their numbers and amounts, and the
balance we owe them; read as a bank statement, its invoice lines became our payments (the invoices
were marked PAID, with the supplier's own statement as the "evidence") and the balance we owe became
our cash. Helm now tells the two apart: a statement issued by a supplier, with no bank about it, is
a supplier statement, kept for reconciliation and never read as cash or as payments.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.helm import ledger
from app.services.helm import vocabulary as v
from tests.services.helm.test_ledger import M, build, doc, of_kind

pytestmark = pytest.mark.no_db

KONKAN = "Konkan Packaging Pvt Ltd"


def _invoice():
    return doc(1, "INVOICE", {"vendor_name": KONKAN, "payment_terms": "Net 30"}, number="KP-1182",
               on=date(2026, 9, 1), total=50_000, vendor=KONKAN)


def _supplier_statement():
    return doc(2, "STATEMENT", {
        "document_classification": "Statement of Account", "vendor_name": KONKAN,
        "customer_name": "Meridian Wholesale Distributors Pvt Ltd", "statement_date": "2026-09-30",
        "closing_balance": "50000.00",
        "transactions": [{"date": "2026-09-01", "description": "Invoice KP-1182", "reference": "KP-1182",
                          "debit": "50000.00"}],
    }, on=date(2026, 9, 30), vendor=KONKAN, text="STATEMENT OF ACCOUNT\nKonkan Packaging Pvt Ltd\nTo: Meridian Wholesale")


def test_a_supplier_statement_never_marks_their_invoices_paid() -> None:
    [payable] = of_kind(build(_invoice(), _supplier_statement()), v.KIND_PAYMENT_DUE)
    assert payable.details["settlement"]["state"] == v.SETTLEMENT_UNPAID
    assert payable.fulfilments == []


def test_a_supplier_statement_balance_is_never_our_cash() -> None:
    assert ledger.statement_balance(_supplier_statement(), None) is None


def test_a_bank_statement_still_settles_and_gives_cash() -> None:
    bank = doc(3, "STATEMENT", {"document_classification": "Bank Statement", "closing_balance": "900000.00",
                                "statement_date": "2026-09-30", "transactions": [
                                    {"date": "2026-09-20", "description": "NEFT KONKAN PACKAGING KP-1182",
                                     "debit": "50000.00"}]},
               on=date(2026, 9, 30), text="HDFC BANK LTD\nStatement of account")
    [payable] = of_kind(build(_invoice(), bank), v.KIND_PAYMENT_DUE)
    assert payable.details["settlement"]["state"] == v.SETTLEMENT_PAID
    assert ledger.statement_balance(bank, None) == ("INR", 900_000 * M, date(2026, 9, 30))


@pytest.mark.parametrize("entities, text, kind", [
    ({"document_classification": "Bank Statement"}, "", ledger.STATEMENT_BANK),
    ({"document_classification": "Statement of Account", "vendor_name": KONKAN}, "", ledger.STATEMENT_SUPPLIER),
    ({"vendor_name": KONKAN}, "STATEMENT OF ACCOUNT\nKonkan Packaging", ledger.STATEMENT_SUPPLIER),
    # A supplier prints their own bank's IFSC for remittance: still their statement, not a bank's.
    ({"vendor_name": KONKAN, "ifsc": "HDFC0001234"}, "", ledger.STATEMENT_SUPPLIER),
    ({"vendor_name": "HDFC Bank Ltd"}, "", ledger.STATEMENT_BANK),
    ({}, "HDFC BANK LTD\nStatement of Account\nAccount No: XXXX6819", ledger.STATEMENT_BANK),
    ({}, "STATEMENT OF ACCOUNT\nFor the period 01-09-2026 to 30-09-2026", ledger.STATEMENT_SUPPLIER),
    ({}, "Supplier statement\nKonkan Packaging", ledger.STATEMENT_SUPPLIER),
    # Bank details at the foot of a supplier's statement do not make it a bank's.
    ({}, "STATEMENT OF ACCOUNT\nKonkan Packaging\nTo: Meridian\nPeriod: September\n...\nBank: HDFC Bank",
     ledger.STATEMENT_SUPPLIER),
    ({}, "Account statement", ledger.STATEMENT_BANK),  # nothing names a supplier: as before, a bank statement
])
def test_which_kind_of_statement(entities, text, kind) -> None:
    assert ledger.statement_kind(doc(9, "STATEMENT", entities, text=text)) == kind


def test_our_own_name_as_issuer_is_the_account_holder_not_a_supplier() -> None:
    """An extractor may put the account holder (us) where an issuer goes; the header still names the bank."""
    ours = doc(9, "STATEMENT", {"company_name": "Meridian Wholesale Distributors Pvt Ltd"},
               text="ICICI BANK\nCurrent account statement")
    assert ledger.statement_kind(ours) == ledger.STATEMENT_SUPPLIER  # without our identities, a named issuer
    from tests.services.helm.test_ledger import US
    assert ledger.statement_kind(ours, US) == ledger.STATEMENT_BANK
