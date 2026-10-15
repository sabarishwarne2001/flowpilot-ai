"""Bank statement import, the pure parts (owner addition A): reading banks' CSV and Excel exports, and
matching their lines to open invoices only when the evidence leaves one answer."""

from __future__ import annotations

import io
from datetime import date

import pytest

from app.services.helm import bank_match as bm
from app.services.helm import bank_parse as bp

M = 1_000_000

HDFC_CSV = (
    "HDFC BANK Ltd\nStatement of account\nAccount No :XXXXXXXX1234\n\n"
    "Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance\n"
    "01/10/26,NEFT-KONKAN PACKAGING PVT LTD-KP-1182,N2610011,01/10/26,\"1,20,000.00\",,\"8,80,000.00\"\n"
    "02/10/26,UPI-SAHYADRIFOODS-INV 2041,U8812,02/10/26,,\"45,000.00\",\"9,25,000.00\"\n"
    "02/10/26,UPI-SAHYADRIFOODS-INV 2041,U8812,02/10/26,,\"45,000.00\",\"9,70,000.00\"\n"
    ",,,,,,\n"
    "STATEMENT SUMMARY,,,,,,\n"
)


def test_a_banks_csv_with_a_preamble_reads_into_signed_lines_and_a_closing_balance() -> None:
    table = bp.read_table("hdfc.csv", HDFC_CSV.encode())
    assert table.columns[0] == "Date" and table.header_row == 4
    mapping = bp.Mapping.of(bp.guess_mapping(table.columns, table.rows))
    assert (mapping.debit, mapping.credit, mapping.balance) == ("Withdrawal Amt.", "Deposit Amt.", "Closing Balance")
    parsed = bp.lines(table, mapping)
    assert [ln.amount_micros for ln in parsed.lines] == [-120_000 * M, 45_000 * M, 45_000 * M]
    assert parsed.lines[0].line_no == 6  # the row number a person sees in a spreadsheet
    assert parsed.lines[0].reference == "N2610011"
    assert bp.closing_balance(parsed) == (date(2026, 10, 2), 970_000 * M)
    # Two genuine identical payments on one day stay two; the occurrence keeps them apart.
    assert [n for _, n in bp.occurrences(parsed.lines)] == [1, 1, 2]


@pytest.mark.parametrize("text, micros", [
    ("1,23,456.78", 123_456_780_000), ("(1,234.00)", -1_234 * M), ("1234.00 Dr", -1_234 * M),
    ("5,000.00 Cr", 5_000 * M), ("-500", -500 * M), ("INR 5,000", 5_000 * M), ("₹ 2,50,000.50", 250_000_500_000),
    ("", None), ("-", None), ("twelve", None),
    # European exports: "1.234,56" is 1,234.56, never 1.23456; grouping that fits no convention is not read.
    ("1.234,56", 1_234_560_000), ("1,2345.00", None), ("12%", None),
])
def test_amounts_as_banks_write_them(text, micros) -> None:
    assert bp.parse_amount(text) == micros


@pytest.mark.parametrize("text, order, on", [
    ("15/10/2026", "DMY", date(2026, 10, 15)), ("15-Oct-26", "DMY", date(2026, 10, 15)),
    ("15 Oct 2026", "DMY", date(2026, 10, 15)), ("2026-10-15", "DMY", date(2026, 10, 15)),
    ("10/15/2026", "MDY", date(2026, 10, 15)), ("46310", "DMY", date(2026, 10, 15)),
    ("31/02/2026", "DMY", None), ("soon", "DMY", None),
])
def test_dates_as_banks_write_them(text, order, on) -> None:
    assert bp.parse_date(text, order) == on


def test_one_amount_column_with_a_dr_cr_column_and_month_first_dates() -> None:
    raw = ("Txn Date,Description,Amount,Dr/Cr,Balance\n"
           "10/13/2026,ACH DEBIT ZENITH STEEL,2500.00,DR,10000.00\n"
           "10/14/2026,TRANSFER IN,500.00,CR,10500.00\n")
    table = bp.read_table("chase.csv", raw.encode())
    guessed = bp.guess_mapping(table.columns, table.rows)
    assert guessed["date_format"] == "MDY" and guessed["drcr"] == "Dr/Cr" and guessed["amount"] == "Amount"
    parsed = bp.lines(table, bp.Mapping.of(guessed))
    assert [(ln.on, ln.amount_micros) for ln in parsed.lines] == [(date(2026, 10, 13), -2_500 * M),
                                                                  (date(2026, 10, 14), 500 * M)]


@pytest.mark.parametrize("flag, direction", [
    ("DR", -1), ("Dr.", -1), ("Debit", -1), ("Withdrawal", -1), ("W", -1),
    ("CR", 1), ("Credit", 1), ("Deposit", 1), ("DEP", 1), ("", None), ("Reversal", None),
])
def test_a_type_column_says_in_or_out_in_the_banks_words(flag, direction) -> None:
    # "Deposit" begins with D: it is money in, never read as a debit.
    assert bp.direction_of(flag) == direction


def test_an_unreadable_line_is_reported_never_guessed() -> None:
    raw = "Date,Narration,Debit,Credit\n01/10/2026,FEE,abc,\n32/10/2026,RENT,5000,\n02/10/2026,BOTH,1,2\n"
    table = bp.read_table("x.csv", raw.encode())
    parsed = bp.lines(table, bp.Mapping.of(bp.guess_mapping(table.columns, table.rows)))
    assert parsed.lines == []
    assert [p["line_no"] for p in parsed.problems] == [2, 3, 4]
    assert "could not be read" in parsed.problems[1]["problem"]


def test_excel_workbooks_read_like_csv() -> None:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["ICICI Bank"])
    sheet.append([])
    sheet.append(["Value Date", "Transaction Remarks", "Withdrawal Amount (INR )", "Deposit Amount (INR )", "Balance (INR )"])
    sheet.append([date(2026, 10, 1), "NEFT KONKAN PACKAGING", 120000, None, 880000])
    buffer = io.BytesIO()
    book.save(buffer)
    table = bp.read_table("icici.xlsx", buffer.getvalue())
    parsed = bp.lines(table, bp.Mapping.of(bp.guess_mapping(table.columns, table.rows)))
    assert [(ln.on, ln.amount_micros, ln.balance_micros) for ln in parsed.lines] == [
        (date(2026, 10, 1), -120_000 * M, 880_000 * M)]


def test_files_that_are_not_statements_are_refused_in_words() -> None:
    with pytest.raises(bp.StatementError, match="No header row"):
        bp.read_table("notes.csv", b"hello,world\n1,2\n")
    with pytest.raises(bp.StatementError, match="CSV or Excel"):
        bp.read_table("statement.pdf", b"%PDF-1.4")
    with pytest.raises(bp.StatementError, match="empty"):
        bp.read_table("empty.csv", b"")
    with pytest.raises(bp.StatementError, match="amount column"):
        bp.Mapping.of({"date": "Date"})


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _payable(number: str, supplier: str, amount: int, *, planned: bool = True, on: date = date(2026, 10, 1),
             held: bool = False, discounted: int | None = None) -> bm.Target:
    return bm.Target(kind=bm.PAYABLE, promise_id=f"p-{number}", work_item_id=f"w-{number}", workspace_id="ws",
                     document=f"invoice-{number}.pdf", document_number=number, counterparty_name=supplier,
                     currency="INR", amount_micros=amount * M, expected_on=on, planned=planned, held=held,
                     hold_reason="Probable duplicate" if held else None,
                     discounted_micros=discounted * M if discounted else None)


def _line(text: str, amount: int, on: date = date(2026, 10, 2), ref: str = "") -> bm.BankLine:
    return bm.BankLine(id=text, on=on, amount_micros=amount * M, description=text, reference=ref, currency="INR")


def test_a_planned_payment_named_in_the_narration_matches_by_itself() -> None:
    [verdict] = bm.match_all([_line("NEFT-KONKAN PACKAGING PVT LTD-KP-1182", -120_000)],
                             [_payable("KP-1182", "Konkan Packaging Pvt Ltd", 120_000)])
    assert verdict.status == bm.AUTO and verdict.target.document_number == "KP-1182"
    assert "the narration names invoice KP-1182" in verdict.reasons


def test_the_payee_and_the_run_window_are_enough_for_a_planned_payment() -> None:
    [verdict] = bm.match_all([_line("UPI/KONKANPACKAGING/OKAXIS", -120_000)],
                             [_payable("KP-1182", "Konkan Packaging Pvt Ltd", 120_000)])
    assert verdict.status == bm.AUTO
    late = bm.match_all([_line("UPI/KONKANPACKAGING/OKAXIS", -120_000, on=date(2026, 11, 20))],
                        [_payable("KP-1182", "Konkan Packaging Pvt Ltd", 120_000)])[0]
    assert late.status == bm.SUGGESTED, "far outside the run window: a person decides"


def test_a_person_decides_when_the_amount_alone_matches_or_two_invoices_tie() -> None:
    [alone] = bm.match_all([_line("NEFT TRANSFER 77812", -50_000)], [_payable("A-1", "Alpha Metals", 50_000)])
    assert alone.status == bm.SUGGESTED and alone.target is None
    tie = bm.match_all([_line("NEFT ALPHA METALS", -50_000)],
                       [_payable("A-1", "Alpha Metals", 50_000), _payable("A-2", "Alpha Metals", 50_000)])[0]
    assert tie.status == bm.SUGGESTED and len(tie.suggestions) == 2


def test_two_lines_never_settle_one_invoice() -> None:
    verdicts = bm.match_all([_line("NEFT KP-1182", -120_000), _line("NEFT KP-1182 RETRY", -120_000)],
                            [_payable("KP-1182", "Konkan Packaging", 120_000)])
    assert [v.status for v in verdicts] == [bm.SUGGESTED, bm.SUGGESTED]


def test_an_unplanned_or_held_invoice_is_only_suggested_and_a_held_one_warns() -> None:
    [unplanned] = bm.match_all([_line("NEFT KP-1182", -120_000)],
                               [_payable("KP-1182", "Konkan Packaging", 120_000, planned=False)])
    assert unplanned.status == bm.SUGGESTED
    [held] = bm.match_all([_line("NEFT KP-1182", -120_000)],
                          [_payable("KP-1182", "Konkan Packaging", 120_000, held=True)])
    assert held.status == bm.SUGGESTED and "was on hold" in held.warning


def test_the_discounted_amount_matches_and_says_so() -> None:
    [verdict] = bm.match_all([_line("NEFT KP-1182", -117_600)],
                             [_payable("KP-1182", "Konkan Packaging", 120_000, discounted=117_600)])
    assert verdict.status == bm.AUTO
    assert "less its early-payment discount" in verdict.reasons[0]


def test_money_in_settles_what_customers_owe_and_no_amount_means_no_match() -> None:
    customer = bm.Target(kind=bm.RECEIVABLE, promise_id="r1", work_item_id="w1", workspace_id="ws",
                         document="inv-2041.pdf", document_number="INV-2041", counterparty_name="Sahyadri Foods",
                         currency="INR", amount_micros=45_000 * M, expected_on=date(2026, 9, 30))
    verdicts = bm.match_all([_line("UPI-SAHYADRIFOODS-INV 2041", 45_000), _line("CASH DEPOSIT", 9_999)],
                            [customer, _payable("KP-1182", "Konkan", 45_000)])
    assert verdicts[0].status == bm.AUTO and verdicts[0].target.kind == bm.RECEIVABLE
    assert verdicts[1].status == bm.NONE and verdicts[1].suggestions == []


@pytest.mark.parametrize("number, text, found", [
    ("KP-1182", "NEFT KP 1182 KONKAN", True), ("KP-1182", "NEFTKONKANKP1182", True),
    ("1182", "NEFT 11823 KONKAN", False), ("1182", "NEFT 1182 KONKAN", True), ("12", "PAID 12 THINGS", False),
])
def test_invoice_numbers_are_found_as_whole_words(number, text, found) -> None:
    assert bm.mentions_document(number, text) is found
