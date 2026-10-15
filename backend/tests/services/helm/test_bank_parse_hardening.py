"""Bank statement reading, hardened (owner addition A): files built to exhaust the server, CSV quirks of
real banks' exports, very large or odd numbers, newest-first files, overdrawn balances and headings that
look alike. Pure: no database."""

from __future__ import annotations

import io
import tracemalloc
import zipfile
from datetime import date

import pytest

from app.services.helm import bank_parse as bp

pytestmark = pytest.mark.no_db

M = 1_000_000


def _peak(call):
    """(what the call returned or raised, the most memory it held in MB)."""
    tracemalloc.start()
    try:
        try:
            outcome = call()
        except bp.StatementError as exc:
            outcome = exc
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return outcome, peak / (1024 * 1024)


def _workbook(rows) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _rezip(data: bytes, replace: dict[str, bytes]) -> bytes:
    """The workbook with some of its parts swapped for hand-written ones."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info.filename, replace.get(info.filename, source.read(info.filename)))
        for name, part in replace.items():
            if name not in source.namelist():
                target.writestr(name, part)
    return out.getvalue()


def _lines_of(raw: str):
    table = bp.read_table("statement.csv", raw.encode())
    mapping = bp.Mapping.of(bp.guess_mapping(table.columns, table.rows))
    return table, mapping, bp.lines(table, mapping)


# ---------------------------------------------------------------------------
# Finding 0: a tiny file must not make the server hold millions of empty cells
# ---------------------------------------------------------------------------


def test_a_header_padded_with_thousands_of_empty_cells_stays_small() -> None:
    # The reviewer's file: 20,000 empty cells after the headings, then 500 blank lines (20 KB).
    data = b"Date,Narration,Amount" + b"," * 20_000 + b"\n" + b"\n" * 500
    outcome, peak = _peak(lambda: bp.read_table("wide.csv", data))
    assert peak < 16, f"reading a {len(data) // 1024} KB file held {peak:.0f} MB"
    assert isinstance(outcome, bp.StatementError) and outcome.reason == "STATEMENT_TOO_WIDE"


def test_excels_padding_to_its_widest_row_is_read_as_the_named_columns() -> None:
    # Excel pads every CSV line to its used range, which can reach its 16,384 columns.
    pad = b"," * 16_380
    data = b"Date,Narration,Amount" + pad + b"\n" + (b"15/10/2026,FEE,10.00" + pad + b"\n") * 100
    table, peak = _peak(lambda: bp.read_table("padded.csv", data))
    assert peak < 16, f"reading a {len(data) // 1024} KB file held {peak:.0f} MB"
    assert isinstance(table, bp.Table) and table.columns == ("Date", "Narration", "Amount")
    assert len(table.rows) == 100 and all(len(row) == 3 for row in table.rows)


def test_one_line_of_a_million_cells_is_refused_without_holding_them() -> None:
    data = b"Date,Narration,Amount\n" + b"," * 1_000_000 + b"\n"
    refused, peak = _peak(lambda: bp.read_table("cells.csv", data))
    assert isinstance(refused, bp.StatementError) and refused.reason == "STATEMENT_TOO_WIDE"
    assert peak < 16, f"one line of {len(data) // 1024} KB held {peak:.0f} MB"


def test_a_header_wider_than_the_column_limit_is_refused_in_words() -> None:
    header = "Date,Narration,Amount," + ",".join(f"Note {i}" for i in range(4, 101))
    with pytest.raises(bp.StatementError) as refused:
        bp.read_table("wide.csv", (header + "\n15/10/2026,FEE,10.00\n").encode())
    assert refused.value.reason == "STATEMENT_TOO_WIDE"
    assert "columns" in refused.value.message


def test_blank_lines_after_the_header_stop_at_the_row_cap() -> None:
    data = b"Date,Narration,Amount\n" + b"\n" * 400_000
    refused, peak = _peak(lambda: bp.read_table("blank.csv", data))
    assert isinstance(refused, bp.StatementError) and refused.reason == "STATEMENT_TOO_LONG"
    assert peak < 16, f"reading {len(data) // 1024} KB of blank lines held {peak:.0f} MB"


def test_a_workbook_that_claims_the_largest_sheet_excel_allows_is_read_within_the_caps(monkeypatch) -> None:
    monkeypatch.setattr(bp, "MAX_ROWS", 300)
    sheet = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             b'<dimension ref="A1:XFD1048576"/><sheetData>'
             b'<row r="1"><c r="A1" t="inlineStr"><is><t>Date</t></is></c>'
             b'<c r="B1" t="inlineStr"><is><t>Narration</t></is></c>'
             b'<c r="C1" t="inlineStr"><is><t>Amount</t></is></c></row>'
             b'<row r="1048576"><c r="A1048576"><v>1</v></c></row></sheetData></worksheet>')
    data = _rezip(_workbook([["x"]]), {"xl/worksheets/sheet1.xml": sheet})
    refused, peak = _peak(lambda: bp.read_table("huge.xlsx", data))
    assert isinstance(refused, bp.StatementError) and refused.reason == "STATEMENT_TOO_LONG"
    assert peak < 16, f"a {len(data)} byte workbook held {peak:.0f} MB"


# ---------------------------------------------------------------------------
# Finding 1: a workbook that unpacks to far more than its size is refused before it is opened
# ---------------------------------------------------------------------------


def test_a_workbook_that_is_a_zip_bomb_is_refused_before_it_is_unpacked() -> None:
    base = _workbook([["Date", "Narration", "Amount"], ["15/10/2026", "FEE", 10]])
    with zipfile.ZipFile(io.BytesIO(base)) as source:
        types, rels = source.read("[Content_Types].xml"), source.read("xl/_rels/workbook.xml.rels")
    # Half a million one-letter strings: about 8 MB unpacked from a few KB, all held in memory on opening.
    bomb = (b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + b"<si><t>a</t></si>" * 500_000 + b"</sst>")
    types = types.replace(b"</Types>", b'<Override PartName="/xl/sharedStrings.xml" ContentType="application/'
                          b'vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
    rels = rels.replace(b"</Relationships>", b'<Relationship Id="rIdStrings" Type="http://schemas.openxmlformats.org/'
                        b'officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
                        b"</Relationships>")
    data = _rezip(base, {"xl/sharedStrings.xml": bomb, "[Content_Types].xml": types,
                         "xl/_rels/workbook.xml.rels": rels})
    assert len(data) < 100_000
    refused, peak = _peak(lambda: bp.read_table("bomb.xlsx", data))
    assert isinstance(refused, bp.StatementError), "the workbook was unpacked and read"
    assert refused.reason == "STATEMENT_TOO_LARGE"
    assert peak < 16


def test_a_workbook_that_unpacks_past_the_size_limit_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(bp, "MAX_XLSX_UNPACKED", 4 * 1024, raising=False)
    data = _workbook([["Date", "Narration", "Amount"]] + [[f"{d:02d}/10/2026", f"NEFT {d}", d] for d in range(1, 29)]
                     * 10)
    with pytest.raises(bp.StatementError) as refused:
        bp.read_table("big.xlsx", data)
    assert refused.value.reason == "STATEMENT_TOO_LARGE"


def test_an_ordinary_workbook_is_still_read() -> None:
    data = _workbook([["Date", "Narration", "Withdrawal", "Deposit", "Balance"],
                      ["14/10/2026", "NEFT ACME", None, 1000, 6000], ["15/10/2026", "UPI FEE", 10, None, 5990]])
    _table = bp.read_table("ok.xlsx", data)
    parsed = bp.lines(_table, bp.Mapping.of(bp.guess_mapping(_table.columns, _table.rows)))
    assert [ln.amount_micros for ln in parsed.lines] == [1_000 * M, -10 * M]


# ---------------------------------------------------------------------------
# Finding 6: CSV quirks are read, or refused in words, never a server error
# ---------------------------------------------------------------------------


def test_a_csv_with_mac_line_endings_is_read() -> None:
    raw = ("Date,Narration,Withdrawal,Deposit,Balance\r01/10/2026,NEFT ACME,100.00,,5000.00\r"
           "02/10/2026,UPI SAHYADRI,,250.00,5250.00\r")
    _, _, parsed = _lines_of(raw)
    assert [ln.amount_micros for ln in parsed.lines] == [-100 * M, 250 * M]


def test_a_stray_carriage_return_in_a_narration_is_read_as_a_space() -> None:
    # The file's lines end in \n (or \r\n), so a lone \r inside a line is not a line break.
    raw = "Date,Narration,Withdrawal,Deposit,Balance\r\n01/10/2026,NEFT\rACME,100.00,,5000.00\r\n"
    _, _, parsed = _lines_of(raw)
    assert [(ln.description, ln.amount_micros) for ln in parsed.lines] == [("NEFT ACME", -100 * M)]
    assert parsed.problems == []


def test_a_csv_that_cannot_be_parsed_is_refused_in_words() -> None:
    raw = 'Date,Narration,Amount\n15/10/2026,"NEFT ' + "x" * 140_000 + "\n"
    with pytest.raises(bp.StatementError) as refused:
        bp.read_table("broken.csv", raw.encode())
    assert refused.value.reason == "STATEMENT_UNREADABLE"
    assert "could not be read" in refused.value.message


# ---------------------------------------------------------------------------
# Finding 7: amounts too large to store are unreadable, not a server error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["12345678901234.00", "99,99,99,99,99,999.00", "-12345678901234"])
def test_amounts_too_large_to_store_are_not_read(text) -> None:
    assert bp.parse_amount(text) is None


def test_the_largest_storable_amount_is_still_read() -> None:
    assert bp.parse_amount("9223372036854.775807") == 9_223_372_036_854_775_807


def test_a_line_with_an_amount_too_large_to_store_is_reported() -> None:
    _, _, parsed = _lines_of("Date,Narration,Withdrawal,Deposit,Balance\n"
                             "15/10/2026,NEFT ACME,12345678901234.00,,99999999999999999.00\n"
                             "15/10/2026,UPI FEE,10.00,,5000.00\n")
    assert [ln.amount_micros for ln in parsed.lines] == [-10 * M]
    assert parsed.problems[0]["line_no"] == 2 and "amount could not be read" in parsed.problems[0]["problem"]


# ---------------------------------------------------------------------------
# Findings 12 and 26: the closing balance follows the file's order, newest first included
# ---------------------------------------------------------------------------


def test_a_newest_first_statement_closes_on_its_first_line() -> None:
    _, _, parsed = _lines_of("Date,Narration,Ref,Withdrawal,Deposit,Balance\n"
                             "15/10/2026,UPI SWIGGY,R3,500.00,,9500.00\n"
                             "15/10/2026,NEFT KONKAN KL-901,R2,1000.00,,10000.00\n"
                             "14/10/2026,NEFT FROM ACME,R1,,1000.00,11000.00\n")
    assert bp.closing_balance(parsed) == (date(2026, 10, 15), 9_500 * M)


def test_a_newest_first_export_with_indian_grouping_closes_on_its_first_line() -> None:
    _, _, parsed = _lines_of("Date,Narration,Ref,Withdrawal,Deposit,Balance\n"
                             '15/10/2026,Payment B,R2,"2,000.00",,"8,000.00"\n'
                             '15/10/2026,Payment A,R1,"1,000.00",,"10,000.00"\n'
                             '14/10/2026,Opening transfer,R0,,"1,000.00","11,000.00"\n')
    assert bp.closing_balance(parsed) == (date(2026, 10, 15), 8_000 * M)


def test_a_back_dated_line_under_a_value_date_mapping_still_closes_on_the_files_last_line() -> None:
    table, mapping, parsed = _lines_of("Value Date,Transaction Date,Narration,Withdrawal,Deposit,Balance\n"
                                       '31/10/2026,31/10/2026,NEFT ACME,,"1,000.00","11,000.00"\n'
                                       '30/10/2026,31/10/2026,INTEREST CREDIT,,50.00,"11,050.00"\n')
    assert mapping.date == "Value Date"
    assert bp.closing_balance(parsed) == (date(2026, 10, 31), 11_050 * M)


def test_an_oldest_first_statement_still_closes_on_its_last_line() -> None:
    _, _, parsed = _lines_of("Date,Narration,Withdrawal,Deposit,Balance\n"
                             "14/10/2026,NEFT FROM ACME,,1000.00,11000.00\n"
                             "15/10/2026,NEFT KONKAN,1000.00,,10000.00\n"
                             "15/10/2026,UPI SWIGGY,500.00,,9500.00\n")
    assert bp.closing_balance(parsed) == (date(2026, 10, 15), 9_500 * M)


def test_one_day_whose_order_cannot_be_told_records_no_closing_balance() -> None:
    _, _, parsed = _lines_of("Date,Narration,Withdrawal,Deposit,Balance\n"
                             "15/10/2026,NEFT KONKAN,1000.00,,10000.00\n"
                             "15/10/2026,UPI SWIGGY,500.00,,7000.00\n")
    assert bp.closing_balance(parsed) is None


# ---------------------------------------------------------------------------
# Finding 15: Excel numbers of ten digits or more, and Excel's float residue for zero
# ---------------------------------------------------------------------------


def test_excel_amounts_and_balances_of_a_billion_or_more_are_read() -> None:
    data = _workbook([["Date", "Narration", "Withdrawal", "Deposit", "Balance"],
                      ["14/10/2026", "NEFT ACME", None, 1000, 999_999_000],
                      ["15/10/2026", "RTGS BIG CUSTOMER", None, 1_500_001_000, 2_500_000_000.0]])
    table = bp.read_table("idr.xlsx", data)
    parsed = bp.lines(table, bp.Mapping.of(bp.guess_mapping(table.columns, table.rows)))
    assert parsed.problems == []
    assert [ln.amount_micros for ln in parsed.lines] == [1_000 * M, 1_500_001_000 * M]
    assert bp.closing_balance(parsed) == (date(2026, 10, 15), 2_500_000_000 * M)


def test_excels_float_residue_for_a_zero_balance_reads_as_zero() -> None:
    data = _workbook([["Date", "Narration", "Withdrawal", "Deposit", "Balance"],
                      ["14/10/2026", "NEFT ACME", None, 1250.1, 1250.1],
                      ["15/10/2026", "TRANSFER OUT", 1250.1, None, 4.547473508864641e-13]])
    table = bp.read_table("zero.xlsx", data)
    parsed = bp.lines(table, bp.Mapping.of(bp.guess_mapping(table.columns, table.rows)))
    assert [ln.balance_micros for ln in parsed.lines] == [1_250_100_000, 0]
    assert bp.closing_balance(parsed) == (date(2026, 10, 15), 0)


# ---------------------------------------------------------------------------
# Finding 16: a balance with its own Dr/Cr column can be overdrawn
# ---------------------------------------------------------------------------


KOTAK = ("Sl. No.,Date,Description,Chq / Ref number,Amount,Dr / Cr,Balance,Dr / Cr\n"
         '1,14/10/2026,OPENING TRANSFER,UTR0,"500.00",CR,"49,000.00",DR\n'
         '2,15/10/2026,NEFT KONKAN KP-1182,UTR1,"1,000.00",DR,"50,000.00",DR\n')


def test_an_overdrawn_balance_with_its_own_dr_cr_column_is_negative() -> None:
    table, mapping, parsed = _lines_of(KOTAK)
    assert (mapping.drcr, mapping.balance, mapping.balance_drcr) == ("Dr / Cr", "Balance", "Dr / Cr (2)")
    assert [ln.amount_micros for ln in parsed.lines] == [500 * M, -1_000 * M]
    assert [ln.balance_micros for ln in parsed.lines] == [-49_000 * M, -50_000 * M]
    assert bp.closing_balance(parsed) == (date(2026, 10, 15), -50_000 * M)


def test_the_balance_dr_cr_column_is_kept_in_the_saved_mapping() -> None:
    mapping = bp.Mapping.of({"date": "Date", "amount": "Amount", "drcr": "Dr / Cr", "balance": "Balance",
                             "balance_drcr": "Dr / Cr (2)"})
    assert mapping.as_json()["balance_drcr"] == "Dr / Cr (2)"
    assert bp.Mapping.of(mapping.as_json()) == mapping
    assert bp.Mapping.of({"date": "Date", "amount": "Amount"}).balance_drcr is None
    assert mapping.missing_from(["Date", "Amount", "Dr / Cr", "Balance"]) == ["Dr / Cr (2)"]


def test_a_balance_whose_dr_cr_says_neither_is_left_out_and_reported() -> None:
    _, _, parsed = _lines_of(KOTAK.replace('"50,000.00",DR', '"50,000.00",??'))
    assert [ln.balance_micros for ln in parsed.lines] == [-49_000 * M, None]
    assert [p["line_no"] for p in parsed.problems] == [3]
    assert "balance" in parsed.problems[0]["problem"]


# ---------------------------------------------------------------------------
# Finding 17: a genuine narration that starts with "Total" is a line, not a footer
# ---------------------------------------------------------------------------


def test_a_genuine_line_whose_narration_starts_with_total_is_kept() -> None:
    _, _, parsed = _lines_of("Date,Narration,Ref,Withdrawal,Deposit,Balance\n"
                             '14/10/2026,TOTAL OIL INDIA PVT LTD NEFT,R0,"5,000.00",,"6,000.00"\n'
                             '15/10/2026,BROUGHT FORWARD INVOICE 12 PAYMENT,R1,,"1,000.00","7,000.00"\n'
                             ',Total,,"5,000.00","1,000.00",\n'
                             "31/10/2026,Closing Balance,,,,\"7,000.00\"\n")
    assert [ln.description for ln in parsed.lines] == ["TOTAL OIL INDIA PVT LTD NEFT",
                                                       "BROUGHT FORWARD INVOICE 12 PAYMENT"]
    assert parsed.problems == [] and parsed.skipped == 2


# ---------------------------------------------------------------------------
# Finding 18: Dr/Cr written against the number
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, micros", [
    ("1234.00Dr", -1_234 * M), ("12,345.67Cr", 12_345_670_000), ("45,678.90CR.", 45_678_900_000),
    ("500.00(Dr)", -500 * M), ("1234.00 Dr", -1_234 * M),
])
def test_dr_and_cr_written_against_the_number_are_read(text, micros) -> None:
    assert bp.parse_amount(text) == micros


def test_a_balance_column_with_cr_against_the_number_gives_a_closing_balance() -> None:
    _, _, parsed = _lines_of("Date,Narration,Withdrawal,Deposit,Balance\n"
                             '14/10/2026,NEFT ACME,,"1,000.00","45,678.90Cr"\n'
                             '15/10/2026,UPI FEE,10.00,,"45,668.90Cr"\n')
    assert bp.closing_balance(parsed) == (date(2026, 10, 15), 45_668_900_000)


def test_a_balance_that_cannot_be_read_is_reported_and_the_line_kept() -> None:
    _, _, parsed = _lines_of("Date,Narration,Withdrawal,Deposit,Balance\n"
                             "15/10/2026,UPI FEE,10.00,,forty\n")
    assert [ln.balance_micros for ln in parsed.lines] == [None]
    assert parsed.problems and "balance" in parsed.problems[0]["problem"]


# ---------------------------------------------------------------------------
# Finding 19: a line with more cells than the header is not read shifted
# ---------------------------------------------------------------------------


def test_a_line_with_more_cells_than_the_header_is_left_out_and_reported() -> None:
    _, _, parsed = _lines_of("Date,Narration,Withdrawal,Deposit,Balance\n"
                             "14/10/2026,NEFT FROM ACME,,1000.00,10000.00\n"
                             "15/10/2026,NEFT TO KONKAN, KP-1182,1000.00,,9000.00\n")
    assert [ln.amount_micros for ln in parsed.lines] == [1_000 * M]
    assert [p["line_no"] for p in parsed.problems] == [3]
    assert "more cells than the header" in parsed.problems[0]["problem"]
    _, _, alone = _lines_of("Date,Narration,Withdrawal,Deposit,Balance\n"
                            "15/10/2026,NEFT TO KONKAN, KP-1182,1000.00,,9000.00\n")
    assert alone.lines == [] and "more cells than the header" in alone.problems[0]["problem"]


def test_a_column_every_line_has_but_the_header_does_not_name_is_a_column() -> None:
    # The bank left the heading of the balance's Cr column out: every line has it, so no line is shifted.
    table, _, parsed = _lines_of("Date,Narration,Withdrawal,Deposit,Balance\n"
                                 "14/10/2026,NEFT FROM ACME,,1000.00,10000.00,Cr\n"
                                 "15/10/2026,UPI FEE,10.00,,9990.00,Cr\n")
    assert table.columns[-1] == "Column 6"
    assert [ln.amount_micros for ln in parsed.lines] == [1_000 * M, -10 * M] and parsed.problems == []


def test_trailing_empty_cells_are_not_a_problem() -> None:
    _, _, parsed = _lines_of("Date,Narration,Withdrawal,Deposit,Balance\n"
                             "15/10/2026,UPI FEE,10.00,,5000.00,,,\n")
    assert [ln.amount_micros for ln in parsed.lines] == [-10 * M] and parsed.problems == []


# ---------------------------------------------------------------------------
# Finding 21: a "Debit/Credit" type column is the Dr/Cr column, not the withdrawals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("heading", ["Debit/Credit", "Debit / Credit Indicator", "Credit/Debit"])
def test_a_debit_credit_type_column_is_guessed_as_dr_cr(heading) -> None:
    table, mapping, parsed = _lines_of(f"Date,Description,Amount,{heading},Balance\n"
                                       '14/10/2026,NEFT KONKAN KP-1182,"50,000.00",Debit,"10,000.00"\n')
    assert (mapping.drcr, mapping.debit, mapping.credit, mapping.amount) == (heading, None, None, "Amount")
    assert [ln.amount_micros for ln in parsed.lines] == [-50_000 * M]


# ---------------------------------------------------------------------------
# Finding 22: a summary row in the preamble is not the header
# ---------------------------------------------------------------------------


def test_a_preamble_summary_row_is_not_taken_as_the_header() -> None:
    table, mapping, parsed = _lines_of("Statement Date,Opening Balance,Closing Balance,Debit Count\n"
                                       '15/10/2026,"10,000.00","8,000.00",2\n'
                                       "\n"
                                       "Date,Narration,Ref,Withdrawal,Deposit,Balance\n"
                                       '14/10/2026,NEFT KONKAN,R1,"1,000.00",,"9,000.00"\n'
                                       '15/10/2026,UPI SWIGGY,R2,"1,000.00",,"8,000.00"\n')
    assert table.header_row == 3 and table.columns[:2] == ("Date", "Narration")
    assert [ln.amount_micros for ln in parsed.lines] == [-1_000 * M, -1_000 * M]
    assert parsed.problems == []


def test_a_header_followed_by_dated_lines_wins_over_an_earlier_lookalike() -> None:
    table = bp.read_table("two.csv", ("Statement Date,Account Description,Opening Balance,Debit Amount\n"
                                      "Generated 16/10/2026,Current account,10000.00,2000.00\n"
                                      "Date,Narration,Withdrawal,Deposit,Balance\n"
                                      "14/10/2026,NEFT KONKAN,1000.00,,9000.00\n"
                                      "15/10/2026,UPI SWIGGY,1000.00,,8000.00\n").encode())
    assert table.header_row == 2
