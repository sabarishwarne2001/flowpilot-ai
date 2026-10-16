"""Supplier statement reconciliation (owner addition B), pure.

Konkan Packaging sends their September statement of account. Each test plants one kind of
difference between their book of our account and ours, and checks Helm names it, with the evidence
on both sides and how it moves their balance against ours.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.helm import reconcile as rc
from app.services.helm import snapshot as snap
from app.services.helm import terms as terms_parser
from app.services.helm import vocabulary as v

pytestmark = pytest.mark.no_db

M = 1_000_000
WS = "00000000-0000-0000-0000-000000000999"
KONKAN = "Konkan Packaging Pvt Ltd"
KEY = "name:konkan packaging"


def invoice(number: str, amount: int, on: date, *, paid_on: date | None = None, paid: int | None = None,
            credit: int = 0, supplier: str = KONKAN, key: str = KEY, reference: str | None = None,
            workspace: str = WS) -> snap.Payable:
    return snap.Payable(
        promise_id=f"p-{number}", work_item_id=f"w-{number}", workspace_id=workspace, document=f"{number}.pdf",
        document_number=number, counterparty_key=key, counterparty_name=supplier, currency="INR",
        amount_micros=(amount - credit) * M, gross_micros=amount * M if credit else None, invoice_date=on,
        due_date=None, received_on=on, terms=terms_parser.parse("net 30"),
        settlement_state=v.SETTLEMENT_PAID if paid_on else v.SETTLEMENT_UNPAID,
        settlement_source=v.SOURCE_STATEMENT if paid_on else None, run_date=None, paid_on=paid_on,
        hold_reason=None, settlement_version=0, confidence=v.CONFIDENCE_VERIFIED, status=v.STATUS_OPEN,
        paid_amount_micros=(paid if paid is not None else amount - credit) * M if paid_on else None,
        paid_reference=reference)


def line(n: int, on: date, reference: str, *, debit: int | None = None, credit: int | None = None,
         description: str = "") -> snap.StatementEntry:
    return snap.StatementEntry(line_no=n, on=on, description=description, reference=reference,
                               debit_micros=debit * M if debit else None, credit_micros=credit * M if credit else None)


def statement(*entries: snap.StatementEntry, closing: int | None, opening: int | None = 0,
              start: date | None = date(2026, 9, 1)) -> snap.SupplierStatement:
    return snap.SupplierStatement(
        work_item_id="w-soa", workspace_id=WS, document="konkan-soa-sep.pdf", counterparty_key=KEY,
        counterparty_name=KONKAN, currency="INR", statement_date=date(2026, 9, 30), period_start=start,
        opening_balance_micros=opening * M if opening is not None else None,
        closing_balance_micros=closing * M if closing is not None else None, entries=tuple(entries),
        confidence=v.CONFIDENCE_EXTRACTED)


def codes(result: rc.Reconciliation) -> list[str]:
    return [d.code for d in result.differences]


def test_a_statement_that_agrees_has_no_differences() -> None:
    ours = [invoice("KP-1182", 50_000, date(2026, 9, 1), paid_on=date(2026, 9, 20)),
            invoice("KP-1190", 30_000, date(2026, 9, 12))]
    theirs = statement(line(1, date(2026, 9, 1), "KP-1182", debit=50_000, description="Invoice"),
                       line(2, date(2026, 9, 21), "NEFT", credit=50_000, description="Payment received KP-1182"),
                       line(3, date(2026, 9, 12), "KP-1190", debit=30_000), closing=30_000)
    result = rc.reconcile(theirs, ours)
    assert result.agrees and result.agreed_lines == 3
    assert result.our_balance_micros == result.their_balance_micros == 30_000 * M


def test_an_invoice_they_billed_that_we_never_received() -> None:
    result = rc.reconcile(statement(line(1, date(2026, 9, 5), "KP-1185", debit=12_000), closing=12_000), [])
    [missing, balance] = result.differences
    assert missing.code == rc.NOT_IN_OUR_BOOKS and missing.effect_micros == 12_000 * M
    assert "KP-1185" in missing.summary and "Ask for a copy" in missing.summary
    assert missing.evidence[0]["side"] == "theirs" and missing.evidence[0]["line"] == 1
    assert balance.code == rc.BALANCE_DIFFERS and "explain all of it" in balance.summary
    assert result.unexplained_micros == 0


def test_the_same_invoice_at_a_different_amount() -> None:
    result = rc.reconcile(statement(line(1, date(2026, 9, 1), "KP-1182", debit=52_000), closing=52_000),
                          [invoice("KP-1182", 50_000, date(2026, 9, 1))])
    assert codes(result) == [rc.AMOUNT_DIFFERS, rc.BALANCE_DIFFERS]
    differs = result.differences[0]
    assert (differs.their_micros, differs.our_micros, differs.effect_micros) == (52_000 * M, 50_000 * M, 2_000 * M)
    assert {e["side"] for e in differs.evidence} == {"theirs", "ours"}


def test_rounding_inside_the_tolerance_agrees() -> None:
    entry = snap.StatementEntry(1, date(2026, 9, 1), "", "KP-1182", 50_000 * M + 400_000, None)
    result = rc.reconcile(statement(entry, closing=None), [invoice("KP-1182", 50_000, date(2026, 9, 1))])
    assert result.agrees


def test_our_invoice_missing_from_their_statement() -> None:
    result = rc.reconcile(statement(closing=0), [invoice("KP-1190", 30_000, date(2026, 9, 12))])
    assert codes(result) == [rc.NOT_ON_THEIR_STATEMENT, rc.BALANCE_DIFFERS]
    assert result.differences[0].effect_micros == -30_000 * M
    assert result.differences[0].evidence[0]["work_item_id"] == "w-KP-1190"


def test_an_invoice_before_the_period_is_their_opening_balance_not_a_difference() -> None:
    old = invoice("KP-1001", 8_000, date(2026, 8, 10))
    result = rc.reconcile(statement(closing=8_000, opening=8_000), [old])
    assert result.agrees


def test_a_payment_we_made_that_they_have_not_applied() -> None:
    ours = [invoice("KP-1182", 50_000, date(2026, 9, 1), paid_on=date(2026, 9, 20), reference="UTR123")]
    result = rc.reconcile(statement(line(1, date(2026, 9, 1), "KP-1182", debit=50_000), closing=50_000), ours)
    assert codes(result) == [rc.PAYMENT_NOT_APPLIED, rc.BALANCE_DIFFERS]
    paid = result.differences[0]
    assert paid.effect_micros == 50_000 * M and "UTR123" in paid.summary
    assert paid.evidence[0]["paid_on"] == "2026-09-20" and paid.evidence[0]["paid_source"] == v.SOURCE_STATEMENT
    assert result.unexplained_micros == 0


def test_a_payment_applied_at_another_amount() -> None:
    ours = [invoice("KP-1182", 50_000, date(2026, 9, 1), paid_on=date(2026, 9, 20))]
    theirs = statement(line(1, date(2026, 9, 1), "KP-1182", debit=50_000),
                       line(2, date(2026, 9, 22), "RCPT", credit=45_000, description="Receipt against KP-1182"),
                       closing=5_000)
    result = rc.reconcile(theirs, ours)
    assert codes(result) == [rc.PAYMENT_AMOUNT_DIFFERS, rc.BALANCE_DIFFERS]
    assert result.differences[0].effect_micros == 5_000 * M


def test_a_credit_they_show_that_we_have_no_record_of() -> None:
    ours = [invoice("KP-1182", 50_000, date(2026, 9, 1))]
    theirs = statement(line(1, date(2026, 9, 1), "KP-1182", debit=50_000),
                       line(2, date(2026, 9, 25), "CHQ 004512", credit=50_000, description="Cheque received"),
                       closing=0)
    result = rc.reconcile(theirs, ours)
    assert codes(result) == [rc.CREDIT_NOT_IN_OUR_BOOKS, rc.BALANCE_DIFFERS]
    unknown = result.differences[0]
    assert "record it before paying again" in unknown.summary and unknown.effect_micros == -50_000 * M


def test_a_credit_note_we_hold_that_they_have_not_applied() -> None:
    ours = [invoice("KP-1182", 50_000, date(2026, 9, 1), credit=5_000)]
    result = rc.reconcile(statement(line(1, date(2026, 9, 1), "KP-1182", debit=50_000), closing=50_000), ours)
    assert codes(result) == [rc.CREDIT_NOT_APPLIED, rc.BALANCE_DIFFERS]
    applied = statement(line(1, date(2026, 9, 1), "KP-1182", debit=50_000),
                        line(2, date(2026, 9, 9), "CN-77", credit=5_000, description="Credit note against KP-1182"),
                        closing=45_000)
    assert rc.reconcile(applied, ours).agrees


def test_the_unexplained_part_of_the_balance_is_said_to_be_unexplained() -> None:
    result = rc.reconcile(statement(line(1, date(2026, 9, 1), "KP-1182", debit=50_000), closing=70_000, opening=20_000),
                          [invoice("KP-1182", 50_000, date(2026, 9, 1))])
    [balance] = result.differences
    assert result.unexplained_micros == 20_000 * M
    assert "not explained by any line" in balance.summary and "opening balance" in balance.summary


def test_another_supplier_or_another_workspace_is_not_compared() -> None:
    others = [invoice("SG-5", 9_000, date(2026, 9, 3), supplier="Shree Ganesh Polymers", key="name:shree ganesh"),
              invoice("KP-1199", 9_000, date(2026, 9, 3), workspace="00000000-0000-0000-0000-000000000111")]
    assert rc.reconcile(statement(closing=0), others).agrees


def test_the_supplier_is_recognised_by_name_when_the_keys_differ() -> None:
    ours = [invoice("KP-1182", 50_000, date(2026, 9, 1), key="tax:27aaack1234f1z5")]
    result = rc.reconcile(statement(line(1, date(2026, 9, 1), "KP-1182", debit=50_000), closing=50_000), ours)
    assert result.agrees


def test_an_invoice_line_without_our_number_matches_on_amount_and_date_alone() -> None:
    ours = [invoice("KP-1182", 50_000, date(2026, 9, 1))]
    theirs = statement(line(1, date(2026, 9, 2), "SI/2026/0457", debit=50_000, description="Sales invoice"),
                       closing=50_000)
    assert rc.reconcile(theirs, ours).agrees


def test_the_json_carries_the_differences_in_their_order() -> None:
    ours = [invoice("KP-1190", 30_000, date(2026, 9, 12))]
    result = rc.reconcile(statement(line(1, date(2026, 9, 5), "KP-1185", debit=12_000), closing=12_000), ours)
    out = result.as_json()
    assert [d["code"] for d in out["differences"]] == [rc.NOT_IN_OUR_BOOKS, rc.NOT_ON_THEIR_STATEMENT,
                                                       rc.BALANCE_DIFFERS]
    assert out["statement_work_item_id"] == "w-soa" and out["their_balance_micros"] == 12_000 * M
    assert out["our_balance_micros"] == 30_000 * M
