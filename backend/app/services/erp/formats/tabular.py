"""ARCH47-S1:tabular — CSV (RFC 4180) and XLSX (ECMA-376) posting files. Pure; standard library only.

One row per line, the header fields repeated on every row (the shape every ERP
"import vendor bills from a spreadsheet" screen expects); a posting without
lines is one row. Columns are the mapping's fields in mapping order: header
fields first, then line fields.

CSV    RFC 4180: CRLF line ends, fields quoted only when they must be, a quote
       doubled. UTF-8, no byte-order mark unless the target asks for one
       (config csv.bom). Delimiter "," or ";" (config csv.delimiter). A text
       value that begins with = + @ or a control character -- or with "-"
       and is not a number -- is prefixed with an apostrophe so a spreadsheet
       never evaluates it (config csv.neutralize_formulas, default on); typed
       numbers are written as numbers.
XLSX   One worksheet, inline strings (no shared-strings part), numbers as
       numbers, dates as date serials with a yyyy-mm-dd format, the header row
       bold and frozen. Written with zipfile with a fixed timestamp and member
       order, so the same posting is the same bytes (its sha256 is stable).
       The package validates against the ECMA-376 SpreadsheetML and OPC
       schemas (verify_arch47 F2).
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence
from xml.sax.saxutils import escape

from app.services.erp.mapping import Record, jsonable

_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_EPOCH = date(1899, 12, 30)
_FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def columns(record: Record) -> list[str]:
    cols = [k for k, _ in record.header]
    seen = set(cols)
    for line in record.lines:
        for k, _ in line:
            if k not in seen:
                seen.add(k)
                cols.append(k)
    return cols


def rows(record: Record) -> list[list[Any]]:
    cols = columns(record)
    head = dict(record.header)
    out: list[list[Any]] = []
    for line in record.lines or [[]]:
        merged = {**head, **dict(line)}
        out.append([merged.get(c) for c in cols])
    return out


def _neutral(text: str) -> str:
    if not text:
        return text
    if text[0] in ("=", "+", "@", "\t", "\r") or (text[0] == "-" and not _NUMBER.match(text)):
        return "'" + text
    return text


def _cell_text(value: Any, neutralize: bool) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, Decimal)):
        return format(value, "f") if isinstance(value, Decimal) else str(value)
    text = str(jsonable(value))
    return _neutral(text) if neutralize and not isinstance(value, (date, datetime)) else text


def to_csv(record: Record, config: Mapping[str, Any] | None = None) -> bytes:
    cfg = dict((config or {}).get("csv") or {})
    delimiter = cfg.get("delimiter", ",")
    if delimiter not in (",", ";"):
        raise ValueError("csv.delimiter is ',' or ';'")
    neutralize = bool(cfg.get("neutralize_formulas", True))
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=delimiter, quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n",
                        doublequote=True)
    writer.writerow(columns(record))
    for row in rows(record):
        writer.writerow([_cell_text(v, neutralize) for v in row])
    data = buf.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if cfg.get("bom") else data


def validate_csv(data: bytes, *, delimiter: str = ",") -> list[str]:
    """RFC 4180 conformance, by an independent reader that follows the ABNF:

        file = record *(CRLF record) [CRLF];  record = field *(COMMA field)
        field = escaped / non-escaped
        escaped = DQUOTE *(TEXTDATA / COMMA / CR / LF / 2DQUOTE) DQUOTE
        non-escaped = *TEXTDATA          (no DQUOTE, COMMA, CR or LF)

    plus: every record has the header's field count, and the file ends with CRLF.
    """
    problems: list[str] = []
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return [f"not UTF-8: {exc}"]
    records: list[list[str]] = []
    field_chars: list[str] = []
    record: list[str] = []
    i, n = 0, len(text)
    state = "start"  # start | plain | quoted | after_quote
    while i < n:
        ch = text[i]
        if state == "quoted":
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    field_chars.append('"')
                    i += 2
                    continue
                state = "after_quote"
            else:
                field_chars.append(ch)
            i += 1
            continue
        if ch == delimiter:
            record.append("".join(field_chars))
            field_chars, state = [], "start"
        elif ch == "\r":
            if i + 1 >= n or text[i + 1] != "\n":
                problems.append(f"a bare CR at offset {i}")
                break
            record.append("".join(field_chars))
            records.append(record)
            record, field_chars, state = [], [], "start"
            i += 2
            continue
        elif ch == "\n":
            problems.append(f"a bare LF at offset {i}")
            break
        elif ch == '"':
            if state == "start":
                state = "quoted"
            else:
                problems.append(f"a quote inside an unquoted field at offset {i}")
                break
        elif state == "after_quote":
            problems.append(f"text after a closing quote at offset {i}")
            break
        else:
            field_chars.append(ch)
            state = "plain"
        i += 1
    if state == "quoted":
        problems.append("an unterminated quoted field")
    if record or field_chars:
        problems.append("the last record does not end with CRLF")
        record.append("".join(field_chars))
        records.append(record)
    if not records:
        return problems + ["no header record"]
    width = len(records[0])
    for k, rec in enumerate(records[1:], start=2):
        if len(rec) != width:
            problems.append(f"record {k} has {len(rec)} fields, the header has {width}")
    return problems


def read_csv(data: bytes, *, delimiter: str = ",") -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), delimiter=delimiter))


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------


def column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _xml(text: str) -> str:
    return escape(_XML_ILLEGAL.sub("", text), {'"': "&quot;"})


def _cell(ref: str, value: Any, style: int = 0) -> str:
    if value is None:
        return ""
    s = f' s="{style}"' if style else ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{s}><v>{int(value)}</v></c>'
    if isinstance(value, (int, Decimal)):
        num = format(value, "f") if isinstance(value, Decimal) else str(value)
        return f'<c r="{ref}" s="2"><v>{num}</v></c>'
    if isinstance(value, (date, datetime)):
        d = value.date() if isinstance(value, datetime) else value
        return f'<c r="{ref}" s="3"><v>{(d - _EPOCH).days}</v></c>'
    # An inline string is never a formula (only an <f> element is): written exactly as mapped.
    text = str(value)
    space = ' xml:space="preserve"' if text != text.strip() else ""
    return f'<c r="{ref}" t="inlineStr"{s}><is><t{space}>{_xml(text)}</t></is></c>'


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    '<Override PartName="/xl/styles.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    '</Types>')
_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="xl/workbook.xml"/></Relationships>')
_WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
    'Target="worksheets/sheet1.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
    'Target="styles.xml"/></Relationships>')
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts>'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/>'
    '</font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '</fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>')


def _workbook(sheet_name: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{_xml(sheet_name)}" sheetId="1" r:id="rId1"/></sheets></workbook>')


def _sheet(header: Sequence[str], body: Sequence[Sequence[Any]]) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
             'state="frozen"/></sheetView></sheetViews><sheetData>']
    parts.append('<row r="1">' + "".join(_cell(f"{column_letter(c)}1", h, 1) for c, h in enumerate(header)) + "</row>")
    for r, row in enumerate(body, start=2):
        parts.append(f'<row r="{r}">' + "".join(_cell(f"{column_letter(c)}{r}", val) for c, val in enumerate(row))
                     + "</row>")
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def to_xlsx(record: Record, config: Mapping[str, Any] | None = None) -> bytes:
    sheet_name = str(((config or {}).get("xlsx") or {}).get("sheet", "Posting"))[:31] or "Posting"
    sheet_name = re.sub(r"[\[\]:*?/\\]", " ", sheet_name)
    members = (("[Content_Types].xml", _CONTENT_TYPES), ("_rels/.rels", _ROOT_RELS),
               ("xl/workbook.xml", _workbook(sheet_name)), ("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS),
               ("xl/styles.xml", _STYLES), ("xl/worksheets/sheet1.xml", _sheet(columns(record), rows(record))))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in members:
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            z.writestr(info, text.encode("utf-8"))
    return buf.getvalue()


def read_xlsx_rows(data: bytes) -> list[list[str]]:
    """An independent reader (for the gates): every row's cell texts / raw values."""
    from app.services.erp.formats import xmlsafe  # ARCH47-S1:xmlsafe (ARCH-16 S1: lxml only in xmlsafe)

    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = xmlsafe.parse(z.read("xl/worksheets/sheet1.xml"))
    out = []
    for row in root.findall(".//m:sheetData/m:row", ns):
        cells: list[str] = []
        for c in row.findall("m:c", ns):
            t = c.find("m:is/m:t", ns)
            v = c.find("m:v", ns)
            letters = "".join(ch for ch in (c.get("r") or "") if ch.isalpha())
            index = 0
            for ch in letters:
                index = index * 26 + (ord(ch.upper()) - 64)
            while letters and len(cells) < index - 1:
                cells.append("")   # an empty cell is simply absent from the sheet
            cells.append((t.text if t is not None else (v.text if v is not None else "")) or "")
        out.append(cells)
    return out


__all__ = ["column_letter", "columns", "read_xlsx_rows", "rows", "to_csv", "to_xlsx", "validate_csv"]
