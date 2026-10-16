"""A bank statement line pays the invoice it names, not one that merely shares a word with it
(found by the 10,000-document performance run).

The ledger settled an invoice from a bank statement line of the same amount when the line shared any
one word of four letters or more with the supplier's name. Indian business names share words
("Shree", "Industries", "Traders", "Enterprises"), so a payment to Shree Balaji Traders marked Shree
Ganesh Steel's invoice of the same amount PAID, whichever was read first, and Balaji's stayed unpaid.
An invoice number was found inside longer numbers too ("INV-12" for "INV-1").
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.helm import vocabulary as v
from tests.services.helm.test_ledger import build, doc, of_kind

pytestmark = pytest.mark.no_db


def _invoice(n: int, supplier: str, number: str, amount: int = 50_000, on: date = date(2026, 9, 1)):
    return doc(n, "INVOICE", {"vendor_name": supplier, "payment_terms": "Net 30"}, number=number, on=on,
               total=amount, vendor=supplier)


def _bank(n: int, *lines: tuple[str, int]):
    return doc(n, "STATEMENT", {"document_classification": "Bank Statement", "closing_balance": "900000",
                                "statement_date": "2026-09-30", "transactions": [
                                    {"date": "2026-09-20", "description": text, "debit": f"{amount}.00"}
                                    for text, amount in lines]},
               on=date(2026, 9, 30), text="HDFC BANK LTD")


def _states(rows) -> dict[str, str]:
    return {r.details["document_number"]: r.details["settlement"]["state"] for r in of_kind(rows, v.KIND_PAYMENT_DUE)}


def test_a_shared_word_in_the_name_is_not_the_payee() -> None:
    ganesh = _invoice(1, "Shree Ganesh Steel Traders", "SGS-101", on=date(2026, 8, 30))  # read first
    balaji = _invoice(2, "Shree Balaji Traders", "SBT-77")
    states = _states(build(ganesh, balaji, _bank(3, ("NEFT SHREE BALAJI TRADERS", 50_000))))
    assert states == {"SGS-101": v.SETTLEMENT_UNPAID, "SBT-77": v.SETTLEMENT_PAID}


def test_a_line_naming_another_suppliers_invoice_does_not_pay_this_one() -> None:
    first = _invoice(1, "Supplier 05 Trading Pvt Ltd", "S05-10005")
    other = _invoice(2, "Supplier 07 Trading Pvt Ltd", "S07-10007", amount=99_000)
    states = _states(build(first, other, _bank(3, ("NEFT SUPPLIER 07 TRADING PVT LTD S07-10007", 50_000))))
    assert states == {"S05-10005": v.SETTLEMENT_UNPAID, "S07-10007": v.SETTLEMENT_UNPAID}


def test_a_line_naming_another_invoice_does_not_pay_this_one() -> None:
    konkan_a = _invoice(1, "Konkan Packaging", "KP-1182")
    konkan_b = _invoice(2, "Konkan Packaging", "KP-1190")
    states = _states(build(konkan_a, konkan_b, _bank(3, ("NEFT KONKAN PACKAGING KP-1190", 50_000))))
    assert states == {"KP-1182": v.SETTLEMENT_UNPAID, "KP-1190": v.SETTLEMENT_PAID}


def test_an_invoice_number_inside_a_longer_number_is_not_named() -> None:
    short = _invoice(1, "Acme Industrial", "INV-1201")
    states = _states(build(short, _bank(2, ("RTGS ACME CORP INV-12015", 50_000))))
    assert states == {"INV-1201": v.SETTLEMENT_UNPAID}


def test_the_payee_named_in_full_or_run_together_still_settles() -> None:
    konkan = _invoice(1, "Konkan Packaging Pvt Ltd", "KP-1182")
    assert _states(build(konkan, _bank(2, ("NEFT KONKAN PACKAGING PVT LTD", 50_000)))) == {
        "KP-1182": v.SETTLEMENT_PAID}
    assert _states(build(konkan, _bank(2, ("UPI/KONKANPACKAGING/OKAXIS", 50_000)))) == {
        "KP-1182": v.SETTLEMENT_PAID}
