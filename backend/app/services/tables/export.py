"""ARCH44-S1:export — CSV, XLSX and JSON of extracted tables. Pure; standard library only.

CSV    RFC 4180, UTF-8 with a byte-order mark (Excel opens it as UTF-8), one
       header line of column paths ("Amount / Debit"), typed values (numbers
       without grouping, dates ISO). A text cell that begins with = + - @ or a
       control character is prefixed with an apostrophe so a spreadsheet never
       evaluates it (CSV injection); typed numbers are emitted as numbers.
XLSX   A minimal, valid Office Open XML workbook written with zipfile: one
       sheet per table, header rows bold and merged exactly as detected,
       numbers as numbers (#,##0.00), dates as date serials, cells that failed
       arithmetic validation shaded, the header frozen. No openpyxl.
JSON   Everything: columns with roles, rows with kind/level/page/parent, cells
       with value, type, confidence and flags, the validation results, and a
       flat `records` list of typed body rows. Decimal values are strings, so
       money survives any JSON parser unrounded.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence
from xml.sax.saxutils import escape

from app.services.tables import vocabulary as v
from app.services.tables.values import format_number

_INJECTION = ("=", "+", "-", "@", "\t", "\r")
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SHEET_ILLEGAL = re.compile(r"[\[\]:*?/\\]")


def _label(column: Any) -> str:
    return " / ".join(column.path) if column.path else column.key


def _typed(cell: Any) -> str:
    if cell is None:
        return ""
    if cell.value_type in v.NUMERIC_TYPES and cell.number is not None:
        return format_number(Decimal(cell.number))
    if cell.value_type == v.TYPE_DATE and cell.when is not None:
        return cell.when.isoformat()
    return cell.text or ""


def safe_text(text: str) -> str:
    """Neutralise spreadsheet formula injection in a TEXT cell."""
    return f"'{text}" if text and text.startswith(_INJECTION) else text


def _index(table: Any) -> dict[tuple[int, int], Any]:
    return {(c.row, c.col): c for c in table.cells}


def to_csv(table: Any) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([safe_text(_label(c)) for c in table.columns])
    cells = _index(table)
    for r in range(table.header_rows, table.n_rows):
        out = []
        for c in range(table.n_cols):
            cell = cells.get((r, c))
            value = _typed(cell)
            numeric = cell is not None and cell.value_type in v.NUMERIC_TYPES and cell.number is not None
            out.append(value if numeric else safe_text(value))
        writer.writerow(out)
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def _json_value(cell: Any) -> Optional[str]:
    if cell is None:
        return None
    if cell.value_type in v.NUMERIC_TYPES and cell.number is not None:
        return format_number(Decimal(cell.number))
    if cell.value_type == v.TYPE_DATE and cell.when is not None:
        return cell.when.isoformat()
    return cell.text or None


def to_dict(table: Any, *, meta: Optional[dict] = None) -> dict:
    cells = _index(table)
    columns = [{"index": c.index, "key": c.key, "path": list(c.path), "type": c.value_type, "role": c.role,
                "role_source": c.role_source} for c in table.columns]
    rows, records = [], []
    for row in table.rows:
        row_cells = {}
        for c in table.columns:
            cell = cells.get((row.index, c.index))
            if cell is None or not cell.text:
                continue
            row_cells[c.key] = {"text": cell.text, "value": _json_value(cell), "type": cell.value_type,
                                "confidence": float(cell.confidence), "flags": list(cell.flags),
                                **({"col_span": cell.col_span} if cell.col_span > 1 else {}),
                                **({"row_span": cell.row_span} if cell.row_span > 1 else {})}
        rows.append({"index": row.index, "kind": row.kind, "level": row.level, "page": row.page,
                     "parent": row.parent, "cells": row_cells})
        if row.kind == v.ROW_BODY:
            records.append({c.key: _json_value(cells.get((row.index, c.index))) for c in table.columns})
    checks = [{"kind": ch.kind, "scope": ch.scope, "outcome": ch.outcome, "row": ch.row, "col": ch.col,
               "expected": None if ch.expected is None else format_number(Decimal(ch.expected)),
               "actual": None if ch.actual is None else format_number(Decimal(ch.actual)),
               "checked": ch.checked, "failed": ch.failed, "message": ch.message} for ch in table.checks]
    head = {"page_start": table.page_start, "page_end": table.page_end, "rows": table.n_rows, "cols": table.n_cols,
            "header_rows": table.header_rows, "method": table.method, "rotation": table.rotation,
            "confidence": float(table.confidence), "status": table.status, "title": table.title,
            "layout_key": table.layout_key, "engine_version": v.ENGINE_VERSION, **(meta or {})}
    return {"table": head, "columns": columns, "rows": rows, "records": records, "validations": checks}


def to_json(table: Any, *, meta: Optional[dict] = None) -> bytes:
    return json.dumps(to_dict(table, meta=meta), ensure_ascii=False, indent=2).encode("utf-8")


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFFDE2E1"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="8">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="4" fontId="1" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="4" fontId="0" fillId="2" borderId="0" xfId="0" applyNumberFormat="1" applyFill="1"/>
<xf numFmtId="0" fontId="0" fillId="2" borderId="0" xfId="0" applyFill="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

S_HEADER, S_DATE, S_NUMBER, S_NUMBER_BOLD, S_BOLD, S_NUMBER_FLAG, S_TEXT_FLAG = 1, 2, 3, 4, 5, 6, 7
_EXCEL_EPOCH = date(1899, 12, 30)


def column_letter(index: int) -> str:
    out, n = "", index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _xml_text(text: str) -> str:
    return escape(_XML_ILLEGAL.sub("", text or ""))


def _cell_xml(ref: str, cell: Any, kind: str) -> str:
    flagged = v.FLAG_ARITH_FAIL in (cell.flags or [])
    bold = kind in (v.ROW_TOTAL, v.ROW_SUBTOTAL, v.ROW_SECTION)
    if cell.value_type in v.NUMERIC_TYPES and cell.number is not None:
        style = S_NUMBER_FLAG if flagged else (S_NUMBER_BOLD if bold else S_NUMBER)
        return f'<c r="{ref}" s="{style}"><v>{format_number(Decimal(cell.number))}</v></c>'
    if cell.value_type == v.TYPE_DATE and cell.when is not None:
        return f'<c r="{ref}" s="{S_DATE}"><v>{(cell.when - _EXCEL_EPOCH).days}</v></c>'
    style = S_TEXT_FLAG if flagged else (S_BOLD if bold else 0)
    return f'<c r="{ref}" t="inlineStr"{f" s={chr(34)}{style}{chr(34)}" if style else ""}><is><t xml:space="preserve">{_xml_text(cell.text)}</t></is></c>'


def _sheet_xml(table: Any) -> str:
    cells = _index(table)
    kinds = {row.index: row.kind for row in table.rows}
    rows_xml, merges = [], []
    header_labels: dict[tuple[int, int], Any] = {}
    for cell in table.cells:
        if cell.row < table.header_rows:
            header_labels[(cell.row, cell.col)] = cell
            if cell.row_span > 1 or cell.col_span > 1:
                merges.append(f"{column_letter(cell.col)}{cell.row + 1}:"
                              f"{column_letter(cell.col + cell.col_span - 1)}{cell.row + cell.row_span}")
    for r in range(table.n_rows):
        parts = []
        for c in range(table.n_cols):
            ref = f"{column_letter(c)}{r + 1}"
            if r < table.header_rows:
                label = header_labels.get((r, c))
                if label is not None:
                    parts.append(f'<c r="{ref}" t="inlineStr" s="{S_HEADER}"><is><t xml:space="preserve">{_xml_text(label.text)}</t></is></c>')
                continue
            cell = cells.get((r, c))
            if cell is not None and cell.text:
                parts.append(_cell_xml(ref, cell, kinds.get(r, v.ROW_BODY)))
                if cell.col_span > 1:
                    merges.append(f"{ref}:{column_letter(c + cell.col_span - 1)}{r + 1}")
        rows_xml.append(f'<row r="{r + 1}">{"".join(parts)}</row>')
    widths = []
    for c in range(table.n_cols):
        longest = max([len(cells[(r, c)].text) for r in range(table.n_rows) if (r, c) in cells and cells[(r, c)].text]
                      + [len(_label(table.columns[c])) if c < len(table.columns) else 8, 8])
        widths.append(f'<col min="{c + 1}" max="{c + 1}" width="{min(60, longest + 2)}" customWidth="1"/>')
    pane = (f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="{table.header_rows}" '
            f'topLeftCell="A{table.header_rows + 1}" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            if table.header_rows else '<sheetViews><sheetView workbookViewId="0"/></sheetViews>')
    merge_xml = f'<mergeCells count="{len(merges)}">{"".join(f"<mergeCell ref={chr(34)}{m}{chr(34)}/>" for m in merges)}</mergeCells>' if merges else ""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'{pane}<cols>{"".join(widths)}</cols><sheetData>{"".join(rows_xml)}</sheetData>{merge_xml}</worksheet>')


def sheet_name(table: Any, used: set[str]) -> str:
    pages = f"p{table.page_start}" if table.page_start == table.page_end else f"p{table.page_start}-{table.page_end}"
    base = _SHEET_ILLEGAL.sub(" ", f"Table {table.ordinal + 1} {pages}")[:31].strip() or "Table"
    name, n = base, 2
    while name.lower() in used:
        suffix = f" ({n})"
        name, n = base[:31 - len(suffix)] + suffix, n + 1
    used.add(name.lower())
    return name


def to_xlsx(tables: Sequence[Any]) -> bytes:
    tables = list(tables)
    if not tables:
        raise ValueError("a workbook needs at least one table")
    used: set[str] = set()
    names = [sheet_name(t, used) for t in tables]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(len(tables)))
                   + '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   '</Relationships>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(names))
                   + '</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                             for i in range(len(tables)))
                   + f'<Relationship Id="rId{len(tables) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   '</Relationships>')
        z.writestr("xl/styles.xml", _STYLES)
        for i, table in enumerate(tables):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(table))
    return buffer.getvalue()


CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
FORMATS = tuple(CONTENT_TYPES)


def filename(base: str, table: Any, fmt: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", (base or "document").rsplit(".", 1)[0])[:80] or "document"
    return f"{stem}_table{table.ordinal + 1}.{fmt}" if table is not None else f"{stem}_tables.{fmt}"


__all__ = ["CONTENT_TYPES", "FORMATS", "column_letter", "filename", "safe_text", "sheet_name", "to_csv", "to_dict",
           "to_json", "to_xlsx"]
