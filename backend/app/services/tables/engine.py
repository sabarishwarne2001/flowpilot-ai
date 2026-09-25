"""ARCH44-S1:engine — pages in, typed tables out. Pure; no I/O.

    pages ──normalise──► upright pages ──grid.page_tables──► per-page tables
          ──continuation──► logical tables (one per real table, across pages)
          ──types, roles──► typed cells ──validate──► flags and confidence

CONTINUATION ACROSS PAGES
=========================

The last table on page p continues as the first table on page p+1 when their
COLUMN SIGNATURES match: the same column count (or the later page's columns
map one-to-one onto the earlier page's by position -- a cheque column that is
empty on one page), the same value type in at least 70% of the columns that
have values on both, and column centres within 6% of the page width. A
repeated header must read the same, and is then dropped; a different header
is a different table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from statistics import mean
from typing import Callable, Mapping, Optional, Sequence

from app.services.tables import grid as G
from app.services.tables import roles as R
from app.services.tables import validate as V
from app.services.tables import values as vals
from app.services.tables import vocabulary as v
from app.services.tables.geometry import PageInput, normalise


@dataclass
class Options:
    lattice: bool = True
    orient: bool = True
    deskew: bool = True
    continuation: bool = True
    validation: bool = True


@dataclass
class HeaderCell:
    row: int
    col: int
    row_span: int
    col_span: int
    text: str
    conf: float
    box: tuple[float, float, float, float]


def build_header(labels: Sequence[G.HeaderLabel], k: int, n_cols: int) -> tuple[list[HeaderCell], list[list[str]]]:
    """Header cells (with spans) and every column's label path."""
    if k == 0:
        return [], [[] for _ in range(n_cols)]
    groups = [lab for lab in labels if lab.b > lab.a]
    cells = [HeaderCell(lab.row, lab.a, 1, lab.b - lab.a + 1, lab.text, lab.conf, lab.box) for lab in groups]
    paths: list[list[str]] = []
    for c in range(n_cols):
        parents = sorted([lab for lab in groups if lab.a <= c <= lab.b], key=lambda lab: lab.row)
        leaves = sorted([lab for lab in labels if lab.a == lab.b == c], key=lambda lab: lab.row)
        text = " ".join(lab.text for lab in leaves)
        top = max((p.row + 1 for p in parents), default=0)
        if leaves and top < k:
            box = (min(l.box[0] for l in leaves), min(l.box[1] for l in leaves),
                   max(l.box[2] for l in leaves), max(l.box[3] for l in leaves))
            cells.append(HeaderCell(top, c, k - top, 1, text, min(l.conf for l in leaves), box))
        paths.append([p.text for p in parents] + ([text] if text else []))
    return sorted(cells, key=lambda x: (x.row, x.col)), paths


@dataclass
class Part:
    raw: G.RawTable
    header: list[HeaderCell]
    paths: list[list[str]]
    types: list[str]
    first_on_page: bool = False
    last_on_page: bool = False


def _part(raw: G.RawTable) -> Part:
    header, paths = build_header(raw.header_labels, raw.header_rows, raw.n_cols)
    types = [vals.infer_column_type([r.cells[c].text for r in raw.body if c in r.cells])[0] for c in range(raw.n_cols)]
    return Part(raw, header, paths, types)


def column_mapping(a: Part, b: Part) -> Optional[list[int]]:
    """b's column index -> a's, or None when the signatures differ."""
    na, nb = a.raw.n_cols, b.raw.n_cols
    width = max(a.raw.page_width, 1.0)
    centres_a = [(x0 + x1) / 2 for x0, x1 in a.raw.columns]
    centres_b = [(x0 + x1) / 2 for x0, x1 in b.raw.columns]
    if na == nb:
        mapping = list(range(nb))
    elif nb < na:
        mapping = []
        for cb in centres_b:
            j = min(range(na), key=lambda j: abs(centres_a[j] - cb))
            if abs(centres_a[j] - cb) > 0.06 * width or j in mapping:
                return None
            mapping.append(j)
    else:
        return None
    close = sum(1 for i, j in enumerate(mapping) if abs(centres_b[i] - centres_a[j]) <= 0.06 * width)
    typed = [(b.types[i], a.types[j]) for i, j in enumerate(mapping)
             if any(c.cells.get(i) for c in b.raw.body) and any(c.cells.get(j) for c in a.raw.body)]
    same = sum(1 for x, y in typed if x == y)
    if b.raw.header_rows:
        want = [R.header_key(a.paths[j]) for j in mapping]
        got = [R.header_key(p) for p in b.paths]
        return mapping if want == got else None
    if typed and same < 0.7 * len(typed):
        return None
    if close < 0.7 * nb:
        return None
    return mapping


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------


@dataclass
class OutColumn:
    index: int
    path: list[str]
    key: str
    header_key: str
    value_type: str
    date_order: str
    role: str
    role_source: str


@dataclass
class OutRow:
    index: int
    kind: str
    level: int
    page: int
    parent: Optional[int] = None


@dataclass
class OutCell:
    row: int
    col: int
    row_span: int
    col_span: int
    page: int
    text: str
    value_type: str
    number: Optional[Decimal]
    when: Optional[date]
    ocr_confidence: float
    base_confidence: float
    confidence: float
    flags: list[str]
    bbox: Optional[tuple[float, float, float, float]]


@dataclass
class OutTable:
    ordinal: int
    page_start: int
    page_end: int
    method: str
    rotation: int
    skew: float
    title: Optional[str]
    n_rows: int
    n_cols: int
    header_rows: int
    columns: list[OutColumn]
    rows: list[OutRow]
    cells: list[OutCell]
    checks: list[V.Check]
    confidence: float
    status: str
    layout_key: str
    bboxes: list[dict]

    def cell(self, row: int, col: int) -> Optional[OutCell]:
        for c in self.cells:
            if c.row == row and c.col == col:
                return c
        return None

    def body_grid(self) -> list[list[str]]:
        index = {(c.row, c.col): c.text for c in self.cells}
        return [[index.get((r, c), "") for c in range(self.n_cols)] for r in range(self.header_rows, self.n_rows)]


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _levels(rows: Sequence[G.RawRow], label_col: int, h: float) -> list[int]:
    xs = [(i, r.cells[label_col].x0) for i, r in enumerate(rows)
          if label_col in r.cells and r.kind in (v.ROW_BODY, v.ROW_SECTION, v.ROW_SUBTOTAL, v.ROW_TOTAL)]
    levels = [0] * len(rows)
    if len(xs) < 2:
        return levels
    labels = G.dbscan_1d([x for _, x in xs], v.INDENT_EPS_H * h)
    for (i, _), label in zip(xs, labels):
        levels[i] = min(label, v.MAX_LEVEL)
    # A row whose label column is empty inherits the level of the row above.
    seen = {i for i, _ in xs}
    for i in range(len(rows)):
        if i not in seen and i > 0:
            levels[i] = levels[i - 1] if rows[i].kind != v.ROW_CARRY else 0
    return levels


def assemble(parts: Sequence[Part], mappings: Sequence[Optional[list[int]]], ordinal: int,
             learned: Optional[Mapping[str, str]], opts: Options) -> OutTable:
    first = parts[0]
    n_cols, k = first.raw.n_cols, first.raw.header_rows
    body: list[G.RawRow] = []
    for part, mapping in zip(parts, mappings):
        for row in part.raw.body:
            if mapping is None:
                body.append(row)
            else:
                body.append(G.RawRow(row.kind, {mapping[c]: cell for c, cell in row.cells.items()}, row.y0, row.y1,
                                     row.page, row.last_cy, row.x_label))
    label_col = G.first_text_column([r for r in body if r.kind == v.ROW_BODY], n_cols)
    levels = _levels(body, label_col, first.raw.h)
    rows = [OutRow(i, v.ROW_HEADER, 0, first.raw.page) for i in range(k)]
    for i, (row, level) in enumerate(zip(body, levels)):
        rows.append(OutRow(k + i, row.kind, level, row.page))
    for i in range(k, len(rows)):
        for j in range(i - 1, k - 1, -1):
            if rows[j].kind == v.ROW_SECTION and rows[j].level < rows[i].level:
                rows[i].parent = j
                break
    value_rows = [r for r in body if r.kind in (v.ROW_BODY, v.ROW_CARRY, v.ROW_SUBTOTAL, v.ROW_TOTAL)]
    typed = [vals.infer_column_type([r.cells[c].text for r in value_rows if c in r.cells]) for c in range(n_cols)]
    types = [t for t, _ in typed]
    orders = [o for _, o in typed]
    paths = first.paths
    keys = R.column_keys(paths)
    role_pairs = R.infer_roles(paths, types, learned)
    columns = [OutColumn(c, paths[c], keys[c], R.header_key(paths[c]), types[c], orders[c], role_pairs[c][0],
                         role_pairs[c][1]) for c in range(n_cols)]

    cells: list[OutCell] = []
    for hc in first.header:
        cells.append(OutCell(hc.row, hc.col, hc.row_span, hc.col_span, first.raw.page, hc.text, v.TYPE_TEXT, None, None,
                             round(hc.conf, 4), round(hc.conf, 4), round(hc.conf, 4), [], hc.box))
    values: dict[tuple[int, int], Decimal] = {}
    for i, row in enumerate(body):
        r = k + i
        for c, cell in sorted(row.cells.items()):
            span = max(1, min(cell.col_span, n_cols - c))
            text = vals.clean(cell.text)
            flags: list[str] = []
            if row.kind == v.ROW_SECTION or span > 1:
                parsed, ok = (vals.TEXT if text else vals.EMPTY), True
            else:
                parsed, ok = vals.parse_cell(text, types[c], orders[c])
            if not ok and row.kind != v.ROW_SECTION:
                flags.append(v.FLAG_TYPE_MISMATCH)
            if cell.ocr_conf < v.LOW_OCR_CONFIDENCE:
                flags.append(v.FLAG_LOW_OCR)
            if cell.spill:
                flags.append(v.FLAG_SPILL)
            base = max(0.0, min(1.0, cell.ocr_conf * cell.geom_conf * (1.0 if ok else 0.6)))
            kind = parsed.kind if text else v.TYPE_EMPTY
            if text and kind == v.TYPE_EMPTY:
                kind = v.TYPE_TEXT  # a printed dash or "NIL": text, never an empty cell with text in it
            if kind in v.NUMERIC_TYPES and parsed.number is not None:
                values[(r, c)] = parsed.number
            cells.append(OutCell(r, c, 1, span, row.page, text, kind,
                                 parsed.number if kind in v.NUMERIC_TYPES else None,
                                 parsed.when if kind == v.TYPE_DATE else None,
                                 round(cell.ocr_conf, 4), round(base, 4), round(base, 4), flags,
                                 (cell.x0, cell.y0, cell.x1, cell.y1)))
    n_rows = k + len(body)
    table = OutTable(ordinal, min(p.raw.page for p in parts), max(p.raw.page for p in parts),
                     first.raw.method if len({p.raw.method for p in parts}) == 1 else v.METHOD_HYBRID,
                     first.raw.rotation, first.raw.skew, first.raw.title, n_rows, n_cols, k, columns, rows, cells, [],
                     0.0, v.STATUS_EXTRACTED, R.layout_key(paths),
                     [{"page": p.raw.page, "x0": round(p.raw.bbox[0], 1), "y0": round(p.raw.bbox[1], 1),
                       "x1": round(p.raw.bbox[2], 1), "y1": round(p.raw.bbox[3], 1),
                       "width": round(p.raw.page_width, 1), "height": round(p.raw.page_height, 1)} for p in parts])
    revalidate(table, enabled=opts.validation)
    return table


def grid_of(table: OutTable) -> V.Grid:
    values = {(c.row, c.col): c.number for c in table.cells if c.number is not None and c.row >= table.header_rows}
    return V.Grid(table.n_rows, table.n_cols, table.header_rows, [r.kind for r in table.rows],
                  [r.level for r in table.rows], [c.value_type for c in table.columns], [c.role for c in table.columns],
                  values, [" / ".join(c.path) or f"column {c.index + 1}" for c in table.columns])


def revalidate(table: OutTable, *, enabled: bool = True) -> V.Result:
    """(Re)run validation over the table's current cells: flags, confidence, status."""
    result = V.validate(grid_of(table)) if enabled else V.Result()
    for cell in table.cells:
        cell.flags = [f for f in cell.flags if f != v.FLAG_ARITH_FAIL]
        if cell.row < table.header_rows:
            continue
        flagged = (cell.row, cell.col) in result.flagged
        if flagged:
            cell.flags.append(v.FLAG_ARITH_FAIL)
        base = 1.0 if v.FLAG_CORRECTED in cell.flags else cell.base_confidence
        cell.confidence = V.adjust(base, result.support.get((cell.row, cell.col), 0), flagged)
    table.checks = result.checks
    body = [c.confidence for c in table.cells if c.row >= table.header_rows and c.text]
    table.confidence = round(mean(body), 4) if body else 0.0
    table.status = V.status_of(result)
    return result


def extract(pages: Sequence[PageInput], *, learned: Optional[Callable[[str], Mapping[str, str]]] = None,
            options: Optional[Options] = None) -> list[OutTable]:
    """Every table in a document, in reading order."""
    opts = options or Options()
    parts: list[Part] = []
    for page_input in sorted(pages, key=lambda p: p.page_number):
        page = normalise(page_input, deskew=opts.deskew, orient=opts.orient)
        found = [_part(raw) for raw in G.page_tables(page, lattice=opts.lattice)]
        if found:
            found[0].first_on_page = True
            found[-1].last_on_page = True
        parts.extend(found)
    chains: list[tuple[list[Part], list[Optional[list[int]]]]] = []
    for part in parts:
        if opts.continuation and chains:
            prev_parts, prev_maps = chains[-1]
            last = prev_parts[-1]
            if part.first_on_page and last.last_on_page and part.raw.page == last.raw.page + 1:
                mapping = column_mapping(prev_parts[0], part)
                if mapping is not None:
                    prev_parts.append(part)
                    prev_maps.append(mapping)
                    continue
        chains.append(([part], [None]))
    tables: list[OutTable] = []
    for ordinal, (chain, maps) in enumerate(chains[:v.MAX_TABLES_PER_DOCUMENT]):
        key = R.layout_key(chain[0].paths)
        tables.append(assemble(chain, maps, ordinal, learned(key) if learned else None, opts))
    return tables


__all__ = ["HeaderCell", "Options", "OutCell", "OutColumn", "OutRow", "OutTable", "assemble", "build_header",
           "column_mapping", "extract", "grid_of", "revalidate"]
