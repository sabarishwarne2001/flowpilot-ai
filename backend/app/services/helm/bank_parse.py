"""Reading a bank statement file into lines (owner addition A): pure functions, no database.

    read_table(filename, data)      CSV, Excel (.xlsx) or old Excel (.xls) -> a header row and its rows
    guess_mapping(columns, rows)    which column is the date, the narration, the amounts... (a guess
                                    the person confirms; the confirmed mapping is saved per account)
    lines(table, mapping)           the statement's lines: date, narration, reference, signed amount
                                    (money in is positive, money out negative), running balance

Banks' exports differ: a preamble of account details before the header row, debit and credit in two
columns or one amount with a Dr/Cr column, Indian digit grouping (1,23,456.78), amounts in brackets,
"Dr"/"Cr" suffixes, dates as 15/10/2026, 15-Oct-26 or Excel serials. Nothing here guesses a value:
a cell that cannot be read is reported as a problem on its line, never filled in.

A file is read within fixed limits (rows, columns, and how far an Excel file may unpack), so a small
file built to exhaust the server is refused in words instead of being read.
"""

from __future__ import annotations

import csv
import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Optional, Sequence

#: The most rows one import reads (a year of a busy current account is a few thousand).
MAX_ROWS = 20_000
#: The largest file one import accepts.
MAX_BYTES = 5 * 1024 * 1024
#: Rows searched for the header line, past the bank's preamble.
HEADER_SEARCH_ROWS = 40
#: The most columns a statement may have (banks use fewer than fifteen). Cells further right are not kept, so a
#: file padded with thousands of empty cells costs no more memory than an ordinary one.
MAX_COLUMNS = 64
#: The most an Excel file may unpack to. An .xlsx is a zip of XML files; a year of a busy account unpacks to a
#: few MB.
MAX_XLSX_UNPACKED = 32 * 1024 * 1024
#: The most parts an Excel file may hold (an ordinary one has about ten).
MAX_XLSX_PARTS = 1_000
#: A part of an Excel file that unpacks to more than this many times its packed size (and to more than 1 MB) is
#: built to exhaust memory (a "zip bomb"); real workbooks pack ten to fifty times.
MAX_XLSX_RATIO = 200
_RATIO_FLOOR = 1024 * 1024
#: The largest amount the database can store, in millionths (a 64-bit whole number): about 9.2 trillion units.
MAX_MICROS = 2**63 - 1

DATE_FORMATS = ("DMY", "MDY", "YMD")


class StatementError(ValueError):
    """The file cannot be read as a statement; the message says why in plain words."""

    def __init__(self, message: str, *, reason: str = "STATEMENT_UNREADABLE") -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


@dataclass(frozen=True)
class Table:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    header_row: int = 0  # 0-based row index of the header in the file


#: The roles a column can have, in the order the mapping lists them.
_COLUMN_ROLES = ("date", "description", "reference", "amount", "debit", "credit", "drcr", "balance", "balance_drcr")


@dataclass(frozen=True)
class Mapping:
    """Which column holds what. Either `amount` (with `drcr` or a sign convention) or `debit`/`credit`."""

    date: str
    description: Optional[str] = None
    reference: Optional[str] = None
    amount: Optional[str] = None
    debit: Optional[str] = None
    credit: Optional[str] = None
    drcr: Optional[str] = None
    balance: Optional[str] = None
    #: A Dr/Cr column for the balance itself (cash-credit and overdraft accounts): "Dr" means overdrawn.
    balance_drcr: Optional[str] = None
    date_format: str = "DMY"
    #: For a single `amount` column without Dr/Cr: are payments out written as negative numbers?
    outflow_negative: bool = True

    def as_json(self) -> dict:
        return {k: getattr(self, k) for k in (*_COLUMN_ROLES, "date_format", "outflow_negative")}

    @classmethod
    def of(cls, raw: dict) -> "Mapping":
        known = {k: raw.get(k) for k in _COLUMN_ROLES}
        known = {k: (str(v) if v not in (None, "") else None) for k, v in known.items()}
        if not known["date"]:
            raise StatementError("Choose the column that holds each line's date.", reason="MAPPING_DATE")
        fmt = str(raw.get("date_format") or "DMY").upper()
        if fmt not in DATE_FORMATS:
            raise StatementError("The date order is day-month-year, month-day-year or year-month-day.",
                                 reason="MAPPING_DATE_FORMAT")
        mapping = cls(**known, date_format=fmt, outflow_negative=bool(raw.get("outflow_negative", True)))
        if not mapping.amount and not (mapping.debit or mapping.credit):
            raise StatementError("Choose the amount column, or the withdrawal and deposit columns.",
                                 reason="MAPPING_AMOUNT")
        return mapping

    def missing_from(self, columns: Sequence[str]) -> list[str]:
        named = [getattr(self, k) for k in _COLUMN_ROLES if getattr(self, k)]
        return [c for c in named if c not in columns]


@dataclass(frozen=True)
class Line:
    line_no: int  # 1-based row number in the file, as the person sees it in a spreadsheet
    on: date
    description: str
    reference: str
    amount_micros: int  # money in > 0, money out < 0
    balance_micros: Optional[int] = None


@dataclass
class Parsed:
    lines: list[Line] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)  # {"line_no", "problem"}
    skipped: int = 0  # blank and total/opening-balance rows


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            return repr(value)
        if value.is_integer():
            return str(int(value))
        # A plain decimal, never "4.547e-13": Excel leaves such a residue where a balance is zero, and money
        # is kept to millionths here, so anything smaller is zero.
        text = format(Decimal(repr(value)).quantize(Decimal("0.000001")).normalize(), "f")
        return "0" if text == "-0" else text
    return str(value).replace("\u00a0", " ").strip()


def _read_limit() -> int:
    """Rows read from a file: the row cap, the preamble, and one more so a file over the cap is noticed."""
    return MAX_ROWS + HEADER_SEARCH_ROWS + 1


def _extent(cells: Sequence[str]) -> int:
    """How many cells a row has up to its last one that holds something."""
    end = len(cells)
    while end and not cells[end - 1]:
        end -= 1
    return end


def _cut(cells: list[str]) -> list[str]:
    """At most MAX_COLUMNS cells. When anything stands further right, the first such cell is kept as one more,
    so a header that is too wide, or a line with more cells than its header, is still noticed."""
    if len(cells) <= MAX_COLUMNS:
        return cells
    beyond = next((c for c in cells[MAX_COLUMNS:] if c), "")
    return cells[:MAX_COLUMNS] + ([beyond] if beyond else [])


#: Excel's own widest row. A CSV line with more cells did not come from a spreadsheet.
_CSV_MAX_CELLS = 16_384


def _physical_lines(text: str, delimiter: str) -> Iterable[str]:
    """The file's lines for the CSV reader, refusing a line with more cells than any spreadsheet has before the
    reader holds them all."""
    for line in io.StringIO(text, newline=""):
        if len(line) > _CSV_MAX_CELLS and line.count(delimiter) >= _CSV_MAX_CELLS:
            raise StatementError(f"A line of this file has more than {_CSV_MAX_CELLS:,} cells; a bank statement has "
                                 "far fewer. Export the statement again.", reason="STATEMENT_TOO_WIDE")
        yield line


def _csv_rows(data: bytes) -> list[list[str]]:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if "\r" in text and "\n" in text:
        # Lines end in \n (or \r\n), so a lone \r inside a narration is not a line break. A file with no \n at
        # all ends its lines in \r, as Excel for Mac's "CSV (Macintosh)" does; the reader handles that below.
        text = re.sub(r"\r(?!\n)", " ", text)
    sample = text[:20_000].replace("\r\n", "\n").replace("\r", "\n")
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows: list[list[str]] = []
    limit = _read_limit()
    reader = csv.reader(_physical_lines(text, dialect.delimiter), dialect)
    try:
        for row in reader:
            rows.append(_cut([c.strip() for c in row]))
            if len(rows) >= limit:
                break
    except csv.Error as exc:
        raise StatementError(f"This CSV file could not be read near row {reader.line_num}: a quote mark may be "
                             "left open, or one cell is far too long. Export the statement again, or save it "
                             "from Excel as CSV or .xlsx.", reason="STATEMENT_UNREADABLE") from exc
    return rows


def _check_workbook_size(data: bytes) -> None:
    """An .xlsx file is a zip of XML files. How big each one unpacks to is written in the zip's directory and
    read here before anything is unpacked, so a small file built to unpack to gigabytes (a "zip bomb") is
    refused at no cost. The zip reader never unpacks a part past the size its directory states."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            parts = archive.infolist()
    except Exception as exc:  # noqa: BLE001 - any failure is "not a readable workbook"
        raise StatementError("This Excel file could not be opened. Save it again from Excel, or as CSV.") from exc
    unpacked = sum(part.file_size for part in parts)
    swollen = any(part.file_size > _RATIO_FLOOR and part.file_size > MAX_XLSX_RATIO * max(part.compress_size, 1)
                  for part in parts)
    if len(parts) > MAX_XLSX_PARTS or unpacked > MAX_XLSX_UNPACKED or swollen:
        raise StatementError("This Excel file unpacks to far more than a bank statement holds, so it was not "
                             "opened. Export the statement again, or save it as CSV.", reason="STATEMENT_TOO_LARGE")


def _xlsx_rows(data: bytes) -> list[list[str]]:
    from openpyxl import load_workbook

    _check_workbook_size(data)
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - any parse failure is "not a readable workbook"
        raise StatementError("This Excel file could not be opened. Save it again from Excel, or as CSV.") from exc
    rows: list[list[str]] = []
    limit = _read_limit()
    try:
        # max_col: a sheet can claim to be 16,384 columns wide, and every row would be padded to that.
        for row in book.worksheets[0].iter_rows(values_only=True, max_col=MAX_COLUMNS + 1):
            cells = _cut([_text(v) for v in row])
            rows.append(cells[:_extent(cells)])
            if len(rows) >= limit:
                break
    except Exception as exc:  # noqa: BLE001 - a sheet that cannot be read to the end is not a readable workbook
        raise StatementError("This Excel file could not be read. Save it again from Excel, or as CSV.") from exc
    finally:
        book.close()
    return rows


def _xls_rows(data: bytes) -> list[list[str]]:
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as exc:  # noqa: BLE001
        raise StatementError("This old-format Excel file (.xls) could not be opened. Save it as .xlsx or "
                             "CSV.") from exc
    sheet = book.sheet_by_index(0)
    rows = []
    for r in range(min(sheet.nrows, _read_limit())):
        out = []
        for c in range(min(sheet.ncols, MAX_COLUMNS + 1)):
            cell = sheet.cell(r, c)
            if cell.ctype == xlrd.XL_CELL_DATE:
                out.append(_text(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode)))
            else:
                out.append(_text(cell.value))
        out = _cut(out)
        rows.append(out[:_extent(out)])
    return rows


_HEADER_WORDS = re.compile(r"\b(date|narration|description|particulars|details|withdrawal|deposit|debit|credit|"
                           r"amount|balance|remarks|ref|reference|cheque|chq)\b", re.I)
#: A header names the narration and an amount; a summary row in the preamble ("Statement Date, Opening Balance,
#: Closing Balance, Debit Count") names dates and balances but not those.
_NARRATION_WORDS = re.compile(r"narration|description|particulars|details|remarks|memo|payee|^transactions?$", re.I)
_AMOUNT_WORDS = re.compile(r"withdrawal|deposit|debit|credit|amount|paid (in|out)|money (in|out)|^(dr|cr)\.?$", re.I)


def _header_index(rows: Sequence[Sequence[str]]) -> int:
    """The header row: a row with a date column and at least two other known words. When several rows read like
    that, a row that also names the narration and an amount wins; among those, the one followed by the most
    dated lines (up to the next header-like row), then the first."""
    found: list[tuple[int, int, bool]] = []  # (row, its date column, names a narration and an amount)
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        cells = [c for c in row if c]
        words = [c for c in cells if _HEADER_WORDS.search(c)]
        at = next((k for k, c in enumerate(row) if c and re.search(r"date", c, re.I)), None)
        if len(words) >= 3 and at is not None:
            full = any(_NARRATION_WORDS.search(c) for c in cells) and any(_AMOUNT_WORDS.search(c) for c in cells)
            found.append((i, at, full))
    if not found:
        raise StatementError("No header row was found (a row naming the date, narration and amount columns). "
                             "Export the statement with column headings.", reason="STATEMENT_NO_HEADER")
    starts = [i for i, _, _ in found]

    def dated(i: int, at: int) -> int:
        end = min([s for s in starts if s > i] + [i + 6])
        return sum(1 for row in rows[i + 1:end]
                   if at < len(row) and any(parse_date(row[at], order) for order in DATE_FORMATS))

    best = [f for f in found if f[2]] or found
    return max(best, key=lambda f: (dated(f[0], f[1]), -f[0]))[0]


def read_table(filename: str, data: bytes) -> Table:
    if not data:
        raise StatementError("The file is empty.", reason="STATEMENT_EMPTY")
    if len(data) > MAX_BYTES:
        raise StatementError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB. Export a shorter period.",
                             reason="STATEMENT_TOO_LARGE")
    name = (filename or "").lower()
    text_file = False
    if name.endswith(".xlsx") or data[:2] == b"PK":
        raw = _xlsx_rows(data)
    elif name.endswith(".xls") or data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raw = _xls_rows(data)
    elif name.endswith((".csv", ".txt", ".tsv")) or not name:
        raw = _csv_rows(data)
        text_file = True
    else:
        raise StatementError("Upload the statement as CSV or Excel (.xlsx or .xls).", reason="STATEMENT_TYPE")
    index = _header_index(raw)
    header = [c.strip() for c in raw[index]]
    named = _extent(header)
    if named > MAX_COLUMNS:
        raise StatementError(f"The header row has more than {MAX_COLUMNS} columns; a bank statement has far fewer. "
                             "Export the statement again, or delete the extra columns.", reason="STATEMENT_TOO_WIDE")
    body_rows = raw[index + 1:]
    if len(body_rows) > MAX_ROWS:
        raise StatementError(f"The statement has more than {MAX_ROWS:,} lines. Export a shorter period.",
                             reason="STATEMENT_TOO_LONG")
    # The columns: every named heading, and unnamed ones as far right as some line holds something. A CSV's
    # header row also sets how many cells a line may have, so a line with more cells than its header (a comma
    # in a narration that is not inside quotes) keeps them, and lines() reports it rather than read it shifted.
    # When most lines are wider than the header, the header lacks its last headings: those are columns too.
    extents = [n for n in (_extent(row) for row in body_rows) if n]
    used = max(extents, default=0)
    longer = sum(1 for n in extents if n > len(header))
    allowed = MAX_COLUMNS
    if text_file and not (longer >= 2 and longer * 2 > len(extents)):
        allowed = len(header)
    width = max(named, min(allowed, used, MAX_COLUMNS))
    header = (header + [""] * width)[:width]
    # Unnamed or repeated headings get a position so every column can be chosen.
    seen: dict[str, int] = {}
    columns = []
    for position, name_ in enumerate(header):
        label = name_ or f"Column {position + 1}"
        seen[label] = seen.get(label, 0) + 1
        columns.append(label if seen[label] == 1 else f"{label} ({seen[label]})")
    body = []
    for row in body_rows:
        cells = list(row[:width]) + [""] * (width - len(row))
        if text_file and _extent(row) > width:
            cells += row[width:]
        body.append(tuple(cells))
    return Table(columns=tuple(columns), rows=tuple(body), header_row=index)


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), start=1)}


#: What a bank writes in a cell that holds no amount.
_NOTHING = ("", "-", "--", "—", "nil", "n/a")
#: A plain number, as Excel cells arrive: read as it is, however many digits (the column was chosen as money,
#: so a long number is an amount, not a cheque or account number).
_PLAIN = re.compile(r"[-+]?\d+(?:\.\d+)?")
#: Dr/Cr written against the number ("1,234.56Cr", "500.00(Dr)"), set apart so it is read like "1,234.56 Cr".
_STUCK_DRCR = re.compile(r"(?i)(\d)\s*\(?(dr|cr)\.?\)?$")


def parse_amount(text: str) -> Optional[int]:
    """'1,23,456.78' / '1.234,56' / '(1,234.00)' / '1234.00 Dr' / '1234.00Dr' / '-500' / 'INR 5,000' -> micros
    (Dr and brackets negative). The table engine's strict number reader decides what a number is: grouping
    that is neither Western, Indian nor European ('1,2345.00') is not read at all, never guessed. An amount
    too large for the database to store (about 9.2 trillion) is not read either."""
    from app.services.tables import vocabulary as tables_vocabulary
    from app.services.tables.values import parse_number

    raw = (text or "").strip()
    if raw.lower() in _NOTHING:
        return None
    raw = _STUCK_DRCR.sub(r"\1 \2", raw)
    if _PLAIN.fullmatch(raw):
        number = Decimal(raw)
    else:
        found = parse_number(raw)
        if found is None or found.number is None or found.kind == tables_vocabulary.TYPE_PERCENT:
            return None
        number = found.number
    micros = (number * 1_000_000).to_integral_value()
    if abs(micros) > MAX_MICROS:
        return None
    return int(micros)


#: What a Dr/Cr ("type") column says, in the words banks use.
_OUT_FLAGS = ("DR", "DEBIT", "WITHDRAWAL", "WDL", "W", "D", "PAID OUT", "OUT")
_IN_FLAGS = ("CR", "CREDIT", "DEPOSIT", "DEP", "C", "PAID IN", "IN")


def direction_of(flag: str) -> Optional[int]:
    """-1 for money out, +1 for money in, None when the flag is not one Helm knows (then it asks)."""
    word = re.sub(r"[^A-Z ]", "", (flag or "").upper()).strip()
    if word in _IN_FLAGS or word.startswith(("CREDIT", "DEPOSIT")):
        return 1
    if word in _OUT_FLAGS or word.startswith(("DEBIT", "WITHDRAW")):
        return -1
    return None


def parse_date(text: str, order: str = "DMY") -> Optional[date]:
    raw = (text or "").strip()
    if not raw:
        return None
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)  # Excel cells and ISO exports
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    if re.fullmatch(r"\d{5}(\.\d+)?", raw):  # an Excel serial that arrived as a number
        return date(1899, 12, 30) + timedelta(days=int(float(raw)))
    named = re.match(r"^(\d{1,2})[\s\-/.]([A-Za-z]{3})[A-Za-z]*[\s\-/.,]+(\d{2,4})$", raw)
    if named and named.group(2).lower() in _MONTHS:
        year = int(named.group(3))
        year = year + 2000 if year < 100 else year
        try:
            return date(year, _MONTHS[named.group(2).lower()], int(named.group(1)))
        except ValueError:
            return None
    parts = re.match(r"^(\d{1,4})[\-/.](\d{1,2})[\-/.](\d{1,4})", raw)
    if not parts:
        return None
    a, b, c = (int(x) for x in parts.groups())
    if order == "YMD" or len(parts.group(1)) == 4:
        year, month, day_ = a, b, c
    elif order == "MDY":
        month, day_, year = a, b, c
    else:
        day_, month, year = a, b, c
    year = year + 2000 if year < 100 else year
    try:
        return date(year, month, day_)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------

#: A heading that says in or out ("Dr/Cr", "Debit / Credit Indicator"), with or without a position ("Dr / Cr (2)").
_DRCR_HEADING = re.compile(r"^(dr\s*/\s*cr|cr\s*/\s*dr|debit\s*/\s*credit|credit\s*/\s*debit)\b", re.I)

_GUESSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("date", (r"^(txn|tran|transaction|value|posting|post)?\.?\s*date$", r"^date$", r"\bdate\b")),
    ("description", (r"narration", r"description", r"particulars", r"details", r"remarks", r"transaction details")),
    ("reference", (r"ref", r"cheque", r"chq", r"utr", r"instrument")),
    # Before the withdrawal and deposit columns: a "Debit/Credit" column says in or out; it holds no amounts.
    ("drcr", (r"^(dr\s*/\s*cr|cr\s*/\s*dr|type|debit\s*/\s*credit|credit\s*/\s*debit)$", _DRCR_HEADING.pattern)),
    ("debit", (r"withdrawal", r"^(?!.*credit).*\bdebit\b", r"^dr\.?$", r"paid out", r"money out")),
    ("credit", (r"deposit", r"^(?!.*debit).*\bcredit\b", r"^cr\.?$", r"paid in", r"money in")),
    ("balance", (r"balance",)),
    ("amount", (r"^amount", r"\bamount\b", r"txn amount")),
)


def guess_mapping(columns: Sequence[str], rows: Sequence[Sequence[str]] = ()) -> dict:
    """A first guess from the headings (and the dates' order from the values); the person confirms it."""
    taken: set[str] = set()
    found: dict[str, Optional[str]] = {}
    # A Dr/Cr column right after the balance says whether the balance is in credit or overdrawn (Kotak and
    # cash-credit accounts); it is set aside so the amount's own Dr/Cr column is not mistaken for it.
    balance_at = next((i for i, c in enumerate(columns) if re.search(r"balance", c, re.I)), None)
    balance_drcr = None
    if balance_at is not None and balance_at + 1 < len(columns) and _DRCR_HEADING.search(columns[balance_at + 1]):
        balance_drcr = columns[balance_at + 1]
        taken.add(balance_drcr)
    for role, patterns in _GUESSES:
        found[role] = None
        for pattern in patterns:
            match = next((c for c in columns if c not in taken and re.search(pattern, c, re.I)), None)
            if match:
                found[role] = match
                taken.add(match)
                break
    if found.get("debit") and found.get("credit"):
        found["amount"] = None  # two amount columns: the single-amount guess is not needed
    if balance_drcr and found.get("balance") != columns[balance_at]:
        balance_drcr = None
    if balance_drcr and found.get("amount") and not found.get("drcr") and not (found.get("debit")
                                                                                or found.get("credit")):
        found["drcr"], balance_drcr = balance_drcr, None  # the file's only Dr/Cr column: the amount needs it more
    found["balance_drcr"] = balance_drcr
    order = "DMY"
    if found.get("date") and rows:
        index = list(columns).index(found["date"])
        for row in rows[:200]:
            m = re.match(r"^(\d{1,2})[\-/.](\d{1,2})[\-/.](\d{2,4})", (row[index] if index < len(row) else "") or "")
            if m and int(m.group(2)) > 12:
                order = "MDY"
                break
            if m and int(m.group(1)) > 12:
                order = "DMY"
                break
    return {**found, "date_format": order, "outflow_negative": True}


# ---------------------------------------------------------------------------
# Lines
# ---------------------------------------------------------------------------

_SKIP = re.compile(r"(?i)^(opening balance|closing balance|total|totals|balance b/?f|balance c/?f|carried forward|"
                   r"brought forward)\b")
#: The same words standing alone ("Total", "Closing Balance:"): a footer or a balance row, whatever else it holds.
_SKIP_ALONE = re.compile(r"(?i)^\W*(opening balance|closing balance|total|totals|balance b/?f|balance c/?f|"
                         r"carried forward|brought forward)\W*$")


def lines(table: Table, mapping: Mapping) -> Parsed:
    missing = mapping.missing_from(table.columns)
    if missing:
        raise StatementError(f"The file has no column called {missing[0]!r}; check the column mapping.",
                             reason="MAPPING_COLUMN")
    at = {name: table.columns.index(name) for name in table.columns}

    def cell(row: Sequence[str], name: Optional[str]) -> str:
        return (row[at[name]] if name and at[name] < len(row) else "") or ""

    width = len(table.columns)
    out = Parsed()
    for offset, row in enumerate(table.rows):
        line_no = table.header_row + offset + 2
        if not any(c.strip() for c in row):
            out.skipped += 1
            continue
        description = re.sub(r"\s+", " ", cell(row, mapping.description)).strip()
        raw_date = cell(row, mapping.date)
        if _SKIP.match(description) or _SKIP.match(raw_date) or (not raw_date.strip() and _SKIP.search(" ".join(row))):
            # "TOTAL OIL INDIA PVT LTD" with a date and an amount is a payment; "Total", or a total or balance
            # row with no amount or no date, is not.
            moved = any(cell(row, c).strip() for c in (mapping.amount, mapping.debit, mapping.credit))
            if (_SKIP_ALONE.match(description) or _SKIP_ALONE.match(raw_date) or not moved
                    or parse_date(raw_date, mapping.date_format) is None):
                out.skipped += 1
                continue
        if len(row) > width and any(c.strip() for c in row[width:]):
            out.problems.append({"line_no": line_no, "problem": "The line has more cells than the header row, so "
                                 "its amounts would be read from the wrong columns; it was left out. Check the "
                                 "narration for a comma that is not inside quotes."})
            continue
        on = parse_date(raw_date, mapping.date_format)
        if on is None:
            if not raw_date.strip() and not any(cell(row, c) for c in (mapping.amount, mapping.debit, mapping.credit)):
                out.skipped += 1  # a wrapped narration line or a footer
                continue
            out.problems.append({"line_no": line_no, "problem": f"The date {raw_date!r} could not be read."})
            continue
        if mapping.amount:
            amount = parse_amount(cell(row, mapping.amount))
            if amount is not None and mapping.drcr:
                direction = direction_of(cell(row, mapping.drcr))
                if direction is None:
                    out.problems.append({"line_no": line_no, "problem": f"The type {cell(row, mapping.drcr)!r} "
                                         "says neither money in nor money out; it was left out."})
                    continue
                amount = abs(amount) * direction
            elif amount is not None and not mapping.outflow_negative:
                amount = -amount
        else:
            debit = parse_amount(cell(row, mapping.debit)) if mapping.debit else None
            credit = parse_amount(cell(row, mapping.credit)) if mapping.credit else None
            if debit and credit:
                out.problems.append({"line_no": line_no,
                                     "problem": "The line has both a withdrawal and a deposit; it was left out."})
                continue
            amount = -abs(debit) if debit else (abs(credit) if credit else None)
        if amount is None or amount == 0:
            out.problems.append({"line_no": line_no, "problem": "The amount could not be read."})
            continue
        balance = None
        balance_text = cell(row, mapping.balance).strip() if mapping.balance else ""
        if balance_text.lower() not in _NOTHING:
            balance = parse_amount(balance_text)
            if balance is None:
                out.problems.append({"line_no": line_no, "problem": f"The balance {balance_text!r} could not be "
                                     "read; the line was kept without it."})
            elif balance and mapping.balance_drcr:
                flag = cell(row, mapping.balance_drcr)
                sign = direction_of(flag)
                if sign is None:
                    out.problems.append({"line_no": line_no, "problem": f"The balance's Dr/Cr {flag!r} says neither "
                                         "in credit nor overdrawn; the line was kept without its balance."})
                    balance = None
                else:
                    balance = abs(balance) * sign  # "Dr" on a balance: the account is overdrawn
        out.lines.append(Line(line_no=line_no, on=on, description=description[:500],
                              reference=re.sub(r"\s+", " ", cell(row, mapping.reference)).strip()[:120],
                              amount_micros=amount, balance_micros=balance))
    return out


def closing_balance(parsed: Parsed) -> Optional[tuple[date, int]]:
    """The statement's closing balance, as of its latest date, when the file carries a balance column.

    Banks export oldest first or newest first, and the closing balance is on the file's last line or its
    first. The running balances tell which: oldest first, each balance is the one before plus the line's
    amount; newest first, each is the one after plus the amount of the line after it. When the balances do
    not tell, the dates do (a first line later than the last means newest first). When neither tells (one
    day whose balances do not follow on), no closing balance is recorded rather than a guess."""
    with_balance = [ln for ln in parsed.lines if ln.balance_micros is not None]
    if not with_balance:
        return None
    first, last = with_balance[0], with_balance[-1]
    pairs = list(zip(with_balance, with_balance[1:]))
    oldest_first = sum(1 for a, b in pairs if b.balance_micros == a.balance_micros + b.amount_micros)
    newest_first = sum(1 for a, b in pairs if a.balance_micros == b.balance_micros + a.amount_micros)
    if len(with_balance) == 1 or oldest_first > newest_first:
        closing = last
    elif newest_first > oldest_first:
        closing = first
    elif first.on != last.on:
        closing = first if first.on > last.on else last
    else:
        return None
    return max(ln.on for ln in with_balance), int(closing.balance_micros)


def occurrences(items: Iterable[Line]) -> list[tuple[Line, int]]:
    """Each line with how many identical lines (date, amount, narration, reference) came before it, so two
    genuine identical payments on one day stay two, and re-importing an overlapping period adds none."""
    counts: dict[tuple, int] = {}
    out = []
    for line in items:
        key = (line.on, line.amount_micros, line.description.lower(), line.reference.lower())
        counts[key] = counts.get(key, 0) + 1
        out.append((line, counts[key]))
    return out


__all__ = ["Line", "MAX_BYTES", "MAX_COLUMNS", "MAX_ROWS", "Mapping", "Parsed", "StatementError", "Table",
           "closing_balance", "direction_of", "guess_mapping", "lines", "occurrences", "parse_amount", "parse_date",
           "read_table"]
