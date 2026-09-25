"""ARCH44-S1:grid — the tables on one page. Pure; no I/O.

CANDIDATE GRIDS
===============

LATTICE  Ruling lines are joined into grids (a horizontal and a vertical rule
         that touch are one component). A component with at least two rules
         each way is a ruled table: vertical rules are the column boundaries,
         horizontal rules the row boundaries, and a rule that stops short of a
         band merges the cells it would have separated (spanning headers).
         Words are placed by their centres, so a table whose cells sit one
         space apart -- which whitespace alone cannot separate -- still splits.
STREAM   Everything not inside a ruled grid. Rows are DBSCAN clusters of token
         centres (eps = 0.45 h, minPts = 1); columns are DBSCAN clusters of the
         body phrases' x-intervals under the interval-gap metric (eps = 0.3 h,
         minPts = 1). Both are computed exactly by a sweep over sorted values
         -- for minPts = 1 DBSCAN is single linkage at eps -- and verify_arch44
         G2 proves the labels equal scikit-learn's DBSCAN on random inputs.
         Phrases that straddle two phrases of another row (headers, long
         narrations) are held out of the clustering and placed afterwards.
HYBRID   Stream columns, rows banded by horizontal rules when the rules
         separate rows (one per row, not a header underline).

HIERARCHY
=========

Header rows are the text-only rows above the data. A header phrase that
covers two or more columns is a group ("Amount" over "Debit | Credit");
single-column labels stacked in one column are one label ("Value" over
"Date"). Every column gets a path: ["Amount", "Debit"].

Body rows are BODY, SECTION (a label alone in the label column), SUBTOTAL,
TOTAL, or CARRY (brought/carried forward). A text-only line under a data row
continues that row's cell (a wrapped narration) when it sits in a text column
right of an empty key column, or closer than a normal row pitch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Optional, Sequence

from app.services.tables import values as vals
from app.services.tables import vocabulary as v
from app.services.tables.geometry import Page, Rule, Token, median_height


# ---------------------------------------------------------------------------
# DBSCAN with minPts = 1, computed by a sweep
# ---------------------------------------------------------------------------


def dbscan_1d(values: Sequence[float], eps: float) -> list[int]:
    """Cluster labels (0..k-1, ordered by value) of 1-D DBSCAN(eps, minPts=1)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    labels = [0] * len(values)
    label = -1
    previous: Optional[float] = None
    for i in order:
        if previous is None or values[i] - previous > eps:
            label += 1
        labels[i] = label
        previous = values[i]
    return labels


def dbscan_intervals(intervals: Sequence[tuple[float, float]], eps: float) -> list[int]:
    """Labels of DBSCAN(eps, minPts=1) under gap(a, b) = max(0, max(a0, b0) - min(a1, b1)).

    Two intervals are neighbours when they overlap or lie within eps; with
    minPts = 1 every point is core, so clusters are the connected components,
    which a sweep over the intervals sorted by start computes exactly.
    """
    order = sorted(range(len(intervals)), key=lambda i: intervals[i][0])
    labels = [0] * len(intervals)
    label = -1
    reach = float("-inf")
    for i in order:
        x0, x1 = intervals[i]
        if x0 - reach > eps:
            label += 1
            reach = x1
        else:
            reach = max(reach, x1)
        labels[i] = label
    return labels


# ---------------------------------------------------------------------------
# Lines and phrases
# ---------------------------------------------------------------------------


@dataclass
class Phrase:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float
    words: list[Token]

    @property
    def w(self) -> float:
        return max(self.x1 - self.x0, 1e-6)

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Line:
    tokens: list[Token]
    phrases: list[Phrase]

    @property
    def y0(self) -> float:
        return min(t.y0 for t in self.tokens)

    @property
    def y1(self) -> float:
        return max(t.y1 for t in self.tokens)

    @property
    def cy(self) -> float:
        return median((t.y0 + t.y1) / 2 for t in self.tokens)

    @property
    def x0(self) -> float:
        return min(t.x0 for t in self.tokens)

    @property
    def x1(self) -> float:
        return max(t.x1 for t in self.tokens)

    @property
    def numeric(self) -> bool:
        return any(vals.numeric_like(p.text) for p in self.phrases)


def phrases_of(tokens: Sequence[Token], h: float, gap_h: float = v.PHRASE_GAP_H) -> list[Phrase]:
    """Words into phrases: joined when the text layer put a real space between
    them (one run of text), or when they sit closer than word spacing."""
    out: list[Phrase] = []
    for t in sorted(tokens, key=lambda t: t.x0):
        gap = t.x0 - out[-1].x1 if out else 0.0
        # Two figures set side by side are two cells, however close: a total
        # debit and a total credit, never one number.
        figures = bool(out) and vals.numeric_like(out[-1].words[-1].text) and vals.numeric_like(t.text)
        if out and ((gap <= gap_h * h and not figures) or (t.space_before and gap <= v.SPACED_GAP_H * h)):
            p = out[-1]
            p.text = f"{p.text} {t.text}"
            p.x0, p.y0, p.x1, p.y1 = min(p.x0, t.x0), min(p.y0, t.y0), max(p.x1, t.x1), max(p.y1, t.y1)
            p.words.append(t)
            p.conf = min(p.conf, t.conf)
        else:
            out.append(Phrase(t.text, t.x0, t.y0, t.x1, t.y1, t.conf, [t]))
    return out


def lines_of(tokens: Sequence[Token], h: float) -> list[Line]:
    if not tokens:
        return []
    labels = dbscan_1d([t.cy for t in tokens], v.ROW_EPS_H * h)
    groups: dict[int, list[Token]] = {}
    for token, label in zip(tokens, labels):
        groups.setdefault(label, []).append(token)
    lines = [Line(sorted(g, key=lambda t: t.x0), phrases_of(g, h)) for _, g in sorted(groups.items())]
    return sorted(lines, key=lambda line: line.cy)


# ---------------------------------------------------------------------------
# Raw tables (one page)
# ---------------------------------------------------------------------------


@dataclass
class RawCell:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    ocr_conf: float
    geom_conf: float = 1.0
    col_span: int = 1
    row_span: int = 1
    spill: bool = False

    def absorb(self, other: "RawCell", sep: str = " ") -> None:
        self.text = f"{self.text}{sep}{other.text}".strip()
        self.x0, self.y0 = min(self.x0, other.x0), min(self.y0, other.y0)
        self.x1, self.y1 = max(self.x1, other.x1), max(self.y1, other.y1)
        self.ocr_conf = min(self.ocr_conf, other.ocr_conf)
        self.geom_conf = min(self.geom_conf, other.geom_conf)
        self.spill = self.spill or other.spill


@dataclass
class RawRow:
    kind: str
    cells: dict[int, RawCell]
    y0: float
    y1: float
    page: int
    last_cy: float = 0.0
    x_label: Optional[float] = None
    level: int = 0

    def text_cols(self) -> list[int]:
        return [c for c, cell in self.cells.items() if cell.text and not vals.numeric_like(cell.text)]

    def numeric_cols(self) -> list[int]:
        return [c for c, cell in self.cells.items() if cell.text and vals.numeric_like(cell.text)]


@dataclass
class HeaderLabel:
    row: int
    a: int
    b: int
    text: str
    conf: float = 1.0
    box: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


@dataclass
class RawTable:
    page: int
    method: str
    columns: list[tuple[float, float]]
    header_labels: list[HeaderLabel]
    header_rows: int
    body: list[RawRow]
    bbox: tuple[float, float, float, float]
    page_width: float
    page_height: float
    rotation: int = 0
    skew: float = 0.0
    title: Optional[str] = None
    h: float = 1.0

    @property
    def n_cols(self) -> int:
        return len(self.columns)


# ---------------------------------------------------------------------------
# Column assignment
# ---------------------------------------------------------------------------


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def place(x0: float, x1: float, columns: Sequence[tuple[float, float]], h: float) -> tuple[int, int, float]:
    """(first column, last column, fraction of the phrase inside the first) for a phrase."""
    width = max(x1 - x0, 1e-6)
    overlaps = [_overlap(x0, x1, c0, c1) for c0, c1 in columns]
    if not any(overlaps):
        gaps = [max(c0 - x1, x0 - c1) for c0, c1 in columns]
        best = min(range(len(columns)), key=lambda j: gaps[j])
        return best, best, max(0.5, 1.0 - gaps[best] / (4 * h))
    main = max(range(len(columns)), key=lambda j: overlaps[j])
    covered = [j for j, o in enumerate(overlaps)
               if o > 0 and (o / max(columns[j][1] - columns[j][0], 1e-6) >= 0.3 or o / width >= 0.25)]
    a, b = min(covered + [main]), max(covered + [main])
    return a, b, overlaps[main] / width


def header_place(x0: float, x1: float, columns: Sequence[tuple[float, float]], h: float) -> tuple[int, int, float]:
    """`place` for header labels: a label centred over the gap between two
    columns, mostly outside both, is a group label over the two."""
    a, b, frac = place(x0, x1, columns, h)
    width = max(x1 - x0, 1e-6)
    lo, hi = a, b
    for j in range(len(columns) - 1):
        g0, g1 = columns[j][1], columns[j + 1][0]
        if g1 <= g0 or not (x0 < (g0 + g1) / 2 < x1):
            continue
        if _overlap(x0, x1, *columns[j]) / width < 0.5 and _overlap(x0, x1, *columns[j + 1]) / width < 0.5:
            lo, hi = min(lo, j), max(hi, j + 1)
    return lo, hi, frac


def _header_like(line: Line, columns: Sequence[tuple[float, float]], h: float) -> bool:
    if line.numeric:
        return False
    spans = [header_place(p.x0, p.x1, columns, h) for p in line.phrases]
    covered = {j for a, b, _ in spans for j in range(a, b + 1)}
    return len(covered) >= max(2, 0.4 * len(columns)) or any(b > a for a, b, _ in spans)


def _row_cells(line: Line, columns: Sequence[tuple[float, float]], h: float) -> dict[int, RawCell]:
    cells: dict[int, RawCell] = {}
    for p in line.phrases:
        a, _, frac = place(p.x0, p.x1, columns, h)
        cell = RawCell(p.text, p.x0, p.y0, p.x1, p.y1, p.conf, min(1.0, frac), spill=frac < 0.95)
        if a in cells:
            cells[a].absorb(cell)
        else:
            cells[a] = cell
    return cells


def _labels_from_lines(lines: Sequence[Line], columns: Sequence[tuple[float, float]], h: float) -> list[HeaderLabel]:
    out: list[HeaderLabel] = []
    for r, line in enumerate(lines):
        for p in line.phrases:
            a, b, _ = header_place(p.x0, p.x1, columns, h)
            if b > a:
                out.append(HeaderLabel(r, a, b, p.text, p.conf, (p.x0, p.y0, p.x1, p.y1)))
                continue
            same = [lab for lab in out if lab.row == r and lab.a == a and lab.b == b]
            if same:
                same[0].text = f"{same[0].text} {p.text}"
            else:
                out.append(HeaderLabel(r, a, a, p.text, p.conf, (p.x0, p.y0, p.x1, p.y1)))
    return out


# ---------------------------------------------------------------------------
# Body rows: kinds and continuations
# ---------------------------------------------------------------------------


def first_text_column(rows: Sequence[RawRow], n_cols: int) -> int:
    for c in range(n_cols):
        texts = [r.cells[c].text for r in rows if c in r.cells and r.cells[c].text]
        if texts and sum(1 for t in texts if not vals.numeric_like(t)) >= 0.6 * len(texts):
            return c
    return 0


def classify_rows(lines: Sequence[tuple[Line, dict[int, RawCell]]], n_cols: int, pitch: float,
                  page: int) -> list[RawRow]:
    """Kinds for body lines; text-only continuation lines fold into the row above."""
    candidates = [RawRow(v.ROW_BODY, cells, line.y0, line.y1, page, line.cy) for line, cells in lines]
    label_col = first_text_column([r for r in candidates if r.numeric_cols()], n_cols)
    rows: list[RawRow] = []
    for row in candidates:
        text = " ".join(c.text for _, c in sorted(row.cells.items()))
        numeric = row.numeric_cols()
        texts = row.text_cols()
        previous = rows[-1] if rows else None
        if v.CARRY_RE.search(text):
            row.kind = v.ROW_CARRY
        elif v.SUBTOTAL_RE.search(text):
            row.kind = v.ROW_SUBTOTAL
        elif numeric and texts and v.TOTAL_RE.search(row.cells[min(texts)].text):
            row.kind = v.ROW_TOTAL
        elif not numeric:
            if previous is not None and previous.kind == v.ROW_BODY and _continues(row, previous, label_col, pitch):
                for c, cell in sorted(row.cells.items()):
                    if c in previous.cells:
                        previous.cells[c].absorb(cell)
                    else:
                        previous.cells[c] = cell
                previous.y1 = max(previous.y1, row.y1)
                previous.last_cy = row.last_cy
                continue
            row.kind = v.ROW_SECTION if set(row.cells) <= {label_col} else v.ROW_BODY
        if label_col in row.cells:
            row.x_label = row.cells[label_col].x0
        rows.append(row)
    return rows


def _continues(row: RawRow, previous: RawRow, label_col: int, pitch: float) -> bool:
    cols = set(row.cells)
    prev_text = set(previous.text_cols())
    if not cols <= prev_text:
        return False
    if label_col > 0 and cols == {label_col} and not any(c < label_col for c in previous.cells if c not in prev_text) \
            and not any(c < label_col for c in cols):
        return True  # a wrapped narration right of an (empty) date column
    if any(c != label_col for c in cols):
        return True  # wrapped text in a secondary text column
    return row.last_cy - previous.last_cy <= v.CONTINUATION_PITCH * pitch


# ---------------------------------------------------------------------------
# Stream tables
# ---------------------------------------------------------------------------


def _blocks(lines: Sequence[Line], h: float) -> list[list[int]]:
    if not lines:
        return []
    diffs = [b.cy - a.cy for a, b in zip(lines, lines[1:])]
    small = [d for d in diffs if d < 3.5 * h]
    pitch = median(small) if small else 1.4 * h
    blocks: list[list[int]] = [[0]]
    for i, d in enumerate(diffs, start=1):
        if d > v.REGION_GAP_PITCH * pitch:
            blocks.append([i])
        else:
            blocks[-1].append(i)
    return blocks


def _summary_line(line: Line) -> bool:
    text = " ".join(p.text for p in line.phrases)
    return bool(v.SUBTOTAL_RE.search(text) or v.CARRY_RE.search(text)
                or any(v.TOTAL_RE.search(p.text) for p in line.phrases))


def _columns(body: Sequence[Line], h: float) -> list[tuple[float, float]]:
    # Totals print wider figures than the rows they add up, wide enough to
    # bridge two right-aligned columns; they are placed, never clustered.
    multi = [line for line in body if len(line.phrases) >= 2 and not _summary_line(line)]
    if len(multi) < 2:
        return []
    # Hold out phrases that straddle two phrases of any other row.
    kept: list[Phrase] = []
    for i, line in enumerate(multi):
        for p in line.phrases:
            straddles = False
            for j, other in enumerate(multi):
                if j == i:
                    continue
                hits = sum(1 for q in other.phrases if _overlap(p.x0, p.x1, q.x0, q.x1) > 0.2 * h)
                if hits >= 2:
                    straddles = True
                    break
            if not straddles:
                kept.append(p)
    if not kept:
        return []
    labels = dbscan_intervals([(p.x0, p.x1) for p in kept], v.COL_EPS_H * h)
    clusters: dict[int, list[Phrase]] = {}
    for p, label in zip(kept, labels):
        clusters.setdefault(label, []).append(p)
    need = max(2, int(v.COL_MIN_SUPPORT * len(multi) + 0.999))
    columns = [(min(p.x0 for p in ps), max(p.x1 for p in ps)) for _, ps in sorted(clusters.items()) if len(ps) >= need]
    return sorted(columns)


def stream_tables(page: Page, tokens: Sequence[Token], h: float, rules: Sequence[Rule]) -> list[RawTable]:
    lines = lines_of(tokens, h)
    tables: list[RawTable] = []
    for block in _blocks(lines, h):
        multi = [i for i in block if len(lines[i].phrases) >= 2]
        if len(multi) < 2:
            continue
        start, end = multi[0], multi[-1]
        region = list(range(start, end + 1))
        # Header lines: leading text-only lines, when data follows.
        header = [i for i in region[:3] if not lines[i].numeric]
        header = header if len(header) < len(region) and header == region[:len(header)] else []
        if not any(lines[i].numeric for i in region):
            header = region[:1]
        body_lines = [lines[i] for i in region if i not in header]
        columns = _columns(body_lines, h)
        if len(columns) < 2:
            continue
        header = [i for i in header if _header_like(lines[i], columns, h)] if header else []
        # A header label over no body column names an empty column (a cheque
        # column on a page with no cheques).
        for i in header:
            for p in lines[i].phrases:
                if not any(_overlap(p.x0, p.x1, c0, c1) > 0 for c0, c1 in columns) and columns[0][0] < p.x0 < columns[-1][1]:
                    columns = sorted(columns + [(p.x0, p.x1)])
        # Header lines the region trimmed (a lone group label such as "Amount").
        above = start - 1
        while above >= 0 and above in block and len(header) < 3 and (not header or header[0] == above + 1) \
                and _extends_header(lines[above], columns, h):
            header.insert(0, above)
            above -= 1
        if header and header[0] < start:
            start = header[0]
        # Trailing lines that still sit in the columns (a wrapped last row).
        while end + 1 in block and all(_overlap(p.x0, p.x1, columns[0][0], columns[-1][1]) > 0 for p in lines[end + 1].phrases) \
                and len(lines[end + 1].phrases) == 1 and lines[end + 1].cy - lines[end].cy <= 1.2 * _pitch(lines, region, h):
            end += 1
        region = list(range(start, end + 1))
        body_idx = [i for i in region if i not in header]
        if not body_idx:
            continue
        pitch = _pitch(lines, body_idx, h)
        h_rules = [r for r in rules if r.horizontal and r.x0 <= columns[0][0] + h and r.x1 >= columns[-1][1] - h
                   and lines[body_idx[0]].y0 - h <= r.y0 <= lines[body_idx[-1]].y1 + h]
        method = v.METHOD_STREAM
        body_pairs = [(lines[i], _row_cells(lines[i], columns, h)) for i in body_idx]
        if len(h_rules) >= max(3, 0.6 * len(body_idx)):
            method = v.METHOD_HYBRID
            body_pairs = _band(body_pairs, sorted({round(r.y0, 1) for r in h_rules}))
        rows = classify_rows(body_pairs, len(columns), pitch, page.page_number)
        if sum(1 for r in rows if r.kind != v.ROW_SECTION) < 2:
            continue
        labels = _labels_from_lines([lines[i] for i in header], columns, h)
        if _key_value(rows, columns):
            continue
        title = None
        t_idx = (header[0] if header else start) - 1
        if t_idx >= 0 and len(lines[t_idx].phrases) == 1 and not lines[t_idx].numeric \
                and lines[start].cy - lines[t_idx].cy <= 3.5 * pitch:
            title = lines[t_idx].phrases[0].text
        x0 = min(min(c[0] for c in columns), min(lines[i].x0 for i in region))
        x1 = max(max(c[1] for c in columns), max(lines[i].x1 for i in region))
        bbox = (x0, lines[start].y0, x1, lines[end].y1)
        tables.append(RawTable(page.page_number, method, columns, labels, len(header), rows, bbox, page.width,
                               page.height, page.rotation, page.skew_degrees, title, h))
    return tables


def _pitch(lines: Sequence[Line], idx: Sequence[int], h: float) -> float:
    multi = [lines[i].cy for i in idx if len(lines[i].phrases) >= 2]
    diffs = [b - a for a, b in zip(multi, multi[1:]) if b - a < 4 * h]
    return median(diffs) if diffs else 1.5 * h


def _extends_header(line: Line, columns: Sequence[tuple[float, float]], h: float) -> bool:
    if line.numeric or not line.phrases:
        return False
    for p in line.phrases:
        if _overlap(p.x0, p.x1, columns[0][0], columns[-1][1]) <= 0:
            return False
    spans = [header_place(p.x0, p.x1, columns, h) for p in line.phrases]
    if len(line.phrases) == 1 and spans[0][0] == 0:
        return False  # a caption above the first column, not a column group
    return any(b > a for a, b, _ in spans) or len(line.phrases) >= 2


def _key_value(rows: Sequence[RawRow], columns: Sequence[tuple[float, float]]) -> bool:
    """A block of 'Label: value' lines is not a table."""
    if len(columns) != 2:
        return False
    labels = [r.cells[0].text for r in rows if 0 in r.cells]
    return bool(labels) and sum(1 for t in labels if t.rstrip().endswith(":")) >= 0.6 * len(labels)


def _band(pairs: list[tuple[Line, dict[int, RawCell]]], ys: Sequence[float]) -> list[tuple[Line, dict[int, RawCell]]]:
    out: list[tuple[Line, dict[int, RawCell]]] = []
    band_of = []
    for line, _ in pairs:
        band_of.append(sum(1 for y in ys if y < line.cy))
    for (line, cells), band in zip(pairs, band_of):
        if out and band == out[-1][2]:  # type: ignore[misc]
            prev_line, prev_cells, _ = out[-1]  # type: ignore[misc]
            for c, cell in cells.items():
                if c in prev_cells:
                    prev_cells[c].absorb(cell)
                else:
                    prev_cells[c] = cell
            prev_line.tokens.extend(line.tokens)
        else:
            out.append((Line(list(line.tokens), list(line.phrases)), dict(cells), band))  # type: ignore[arg-type]
    return [(line, cells) for line, cells, _ in out]  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Lattice tables
# ---------------------------------------------------------------------------


def _merge_rules(rules: Sequence[Rule], tol: float) -> tuple[list[Rule], list[Rule]]:
    hs = sorted([r for r in rules if r.horizontal], key=lambda r: (round(r.y0 / tol), r.x0))
    vs = sorted([r for r in rules if not r.horizontal], key=lambda r: (round(r.x0 / tol), r.y0))

    def merge(items: list[Rule], horizontal: bool) -> list[Rule]:
        out: list[Rule] = []
        for r in items:
            for i, m in enumerate(out):
                if horizontal and abs(m.y0 - r.y0) <= tol and r.x0 <= m.x1 + tol and r.x1 >= m.x0 - tol:
                    out[i] = Rule(min(m.x0, r.x0), (m.y0 + r.y0) / 2, max(m.x1, r.x1), (m.y0 + r.y0) / 2)
                    break
                if not horizontal and abs(m.x0 - r.x0) <= tol and r.y0 <= m.y1 + tol and r.y1 >= m.y0 - tol:
                    out[i] = Rule((m.x0 + r.x0) / 2, min(m.y0, r.y0), (m.x0 + r.x0) / 2, max(m.y1, r.y1))
                    break
            else:
                out.append(r)
        return out

    return merge(hs, True), merge(vs, False)


def _grids(rules: Sequence[Rule], tol: float) -> list[tuple[list[Rule], list[Rule]]]:
    hs, vs = _merge_rules(rules, tol)
    items = [("h", r) for r in hs] + [("v", r) for r in vs]
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (ki, a) in enumerate(items):
        if ki != "h":
            continue
        for j, (kj, b) in enumerate(items):
            if kj != "v":
                continue
            if a.x0 - tol <= b.x0 <= a.x1 + tol and b.y0 - tol <= a.y0 <= b.y1 + tol:
                parent[find(i)] = find(j)
    comps: dict[int, tuple[list[Rule], list[Rule]]] = {}
    for i, (k, r) in enumerate(items):
        comps.setdefault(find(i), ([], []))[0 if k == "h" else 1].append(r)
    return [c for c in comps.values() if len(c[0]) >= 2 and len(c[1]) >= 2]


def _unique(values: Sequence[float], tol: float) -> list[float]:
    out: list[float] = []
    for x in sorted(values):
        if not out or x - out[-1] > tol:
            out.append(x)
    return out


def _covers(rule: Rule, a: float, b: float, tol: float) -> bool:
    if rule.horizontal:
        return rule.x0 - tol <= a and rule.x1 + tol >= b
    return rule.y0 - tol <= a and rule.y1 + tol >= b


def lattice_tables(page: Page, tokens: Sequence[Token], h: float) -> tuple[list[RawTable], set[int]]:
    scale = v.DPI / 72.0
    tol = v.RULE_TOL_PT * scale
    tables: list[RawTable] = []
    used: set[int] = set()
    for hs, vs in _grids(page.rules, tol):
        xs = _unique([r.x0 for r in vs], tol)
        ys = _unique([r.y0 for r in hs], tol)
        if len(xs) < 3 or len(ys) < 3:
            continue
        inside = [i for i, t in enumerate(tokens) if xs[0] - tol <= t.cx <= xs[-1] + tol and ys[0] - tol <= t.cy <= ys[-1] + tol]
        if not inside:
            continue
        n_cols, n_bands = len(xs) - 1, len(ys) - 1
        # Place words (splitting an OCR segment that crosses a column rule).
        grid: dict[tuple[int, int], list[Token]] = {}
        for i in inside:
            for piece in _split_at(tokens[i], xs[1:-1], h):
                col = max(0, min(n_cols - 1, sum(1 for x in xs[1:-1] if x < piece.cx)))
                band = max(0, min(n_bands - 1, sum(1 for y in ys[1:-1] if y < piece.cy)))
                grid.setdefault((band, col), []).append(piece)
        used.update(inside)

        def v_covers(boundary: int, band: int) -> bool:
            x = xs[boundary]
            return any(abs(r.x0 - x) <= tol and _covers(r, ys[band] + tol, ys[band + 1] - tol, tol) for r in vs)

        def cell_text(ws: list[Token]) -> tuple[str, float, tuple[float, float, float, float]]:
            lines = lines_of(ws, h)
            text = " ".join(" ".join(t.text for t in sorted(line.tokens, key=lambda t: t.x0)) for line in lines)
            box = (min(t.x0 for t in ws), min(t.y0 for t in ws), max(t.x1 for t in ws), max(t.y1 for t in ws))
            return text, min(t.conf for t in ws), box

        bands: list[list[tuple[int, int, str, float, tuple]]] = []
        for band in range(n_bands):
            groups: list[list[int]] = [[0]]
            for c in range(1, n_cols):
                if v_covers(c, band):
                    groups.append([c])
                else:
                    groups[-1].append(c)
            row = []
            for g in groups:
                ws = [t for c in g for t in grid.get((band, c), [])]
                if ws:
                    text, conf, box = cell_text(ws)
                    row.append((g[0], g[-1], text, conf, box))
            bands.append(row)
        numeric = [any(vals.numeric_like(t) for _, _, t, _, _ in row) for row in bands]
        header_n = 0
        if any(numeric):
            while header_n < min(3, n_bands - 1) and not numeric[header_n] and bands[header_n]:
                header_n += 1
        labels: list[HeaderLabel] = []
        for r in range(header_n):
            for a, b, text, conf, box in bands[r]:
                # Row spans follow from the hierarchy: a leaf label with no group
                # above it fills every header band (build_header).
                labels.append(HeaderLabel(r, a, b, text, conf, box))
        body_pairs: list[tuple[Line, dict[int, RawCell]]] = []
        for r in range(header_n, n_bands):
            if not bands[r]:
                continue
            cells = {a: RawCell(text, *box, conf, 1.0, col_span=b - a + 1) for a, b, text, conf, box in bands[r]}
            ws = [t for (band, _), toks in grid.items() if band == r for t in toks]
            body_pairs.append((Line(ws, []), cells))
        columns = [(xs[c], xs[c + 1]) for c in range(n_cols)]
        pitch = median([ys[i + 1] - ys[i] for i in range(n_bands)]) if n_bands else 1.5 * h
        rows = classify_rows(body_pairs, n_cols, pitch, page.page_number)
        # Ruled rows already group wrapped lines; a text-only ruled row is a row.
        if len(rows) < 1 or n_cols < 2:
            continue
        tables.append(RawTable(page.page_number, v.METHOD_LATTICE, columns, labels, header_n, rows,
                               (xs[0], ys[0], xs[-1], ys[-1]), page.width, page.height, page.rotation,
                               page.skew_degrees, None, h))
    return tables, used


def _split_at(token: Token, cuts: Sequence[float], h: float) -> list[Token]:
    """An OCR segment that crosses a column rule is cut at the nearest space."""
    crossing = [x for x in cuts if token.x0 + 0.2 * h < x < token.x1 - 0.2 * h]
    if not crossing or " " not in token.text:
        return [token]
    x = crossing[0]
    share = (x - token.x0) / max(token.w, 1e-6)
    target = share * len(token.text)
    spaces = [i for i, ch in enumerate(token.text) if ch == " "]
    cut = min(spaces, key=lambda i: abs(i - target))
    left, right = token.text[:cut].strip(), token.text[cut + 1:].strip()
    if not left or not right:
        return [token]
    xm = token.x0 + token.w * (cut / len(token.text))
    first = Token(left, token.x0, token.y0, xm, token.y1, token.conf, token.page)
    second = Token(right, xm, token.y0, token.x1, token.y1, token.conf, token.page)
    return [first] + _split_at(second, cuts, h)


# ---------------------------------------------------------------------------
# One page
# ---------------------------------------------------------------------------


def page_tables(page: Page, *, lattice: bool = True) -> list[RawTable]:
    tokens = [t for t in page.tokens if t.text.strip()]
    if not tokens:
        return []
    h = median_height(tokens)
    found: list[RawTable] = []
    used: set[int] = set()
    if lattice and page.rules:
        found, used = lattice_tables(page, tokens, h)
    rest = [t for i, t in enumerate(tokens) if i not in used]
    found += stream_tables(page, rest, h, page.rules)
    return sorted(found, key=lambda t: t.bbox[1])


__all__ = ["HeaderLabel", "Line", "Phrase", "RawCell", "RawRow", "RawTable", "classify_rows", "dbscan_1d",
           "dbscan_intervals", "first_text_column", "lattice_tables", "lines_of", "page_tables", "phrases_of",
           "place", "stream_tables"]
