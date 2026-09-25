"""ARCH45-S1:lines — line items aligned row to row across documents. Pure; no I/O.

Where a document has ARCH-44 tables, its line items are the BODY rows of every
table that has a description column and at least one of quantity, unit price
or amount (column roles, inferred, learned or set by a reviewer); each row
keeps its page and the union box of its cells, so the viewer can point at it.
Otherwise the lines come from `extracted_entities` through ARCH-31's
line_extraction (the same alias-tolerant reader three-way matching uses).
A document with no line items at all takes no part in this layer: a delivery
note with no priced lines is not "missing" every line of the invoice.

Two rows score

    0.60 * description similarity (encoder cosine + token Dice, as clauses)
  + 0.25 * code agreement (both have a code: equal 1, different 0; else the
           description weight takes its share)
  + 0.15 * the share of quantity / unit price / amount present on both that agree

and are aligned by the Hungarian assignment (LINE_MATCH_MIN), then grouped
across documents exactly like clauses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import combinations
from typing import Any, Mapping, Optional, Sequence

from app.services.corroboration import materiality as mat
from app.services.corroboration import normalize as n
from app.services.corroboration import vocabulary as v
from app.services.corroboration.segment import Span

ATTRIBUTES = ("quantity", "unit_price", "amount")
#: A row with an amount but no quantity and no rate whose description is a
#: tax, a total or a rounding entry is a summary row the table extractor did
#: not class as one ("GST @ 18%", "Round off"): not a line item.
SUMMARY_ROW = re.compile(r"\b(gst|igst|cgst|sgst|vat|tax(?:es)?|cess|tds|sub\s*-?\s*total|total|round(?:ing)?\s*off|"
                         r"discount|grand|net\s+payable|amount\s+due|balance\s+due)\b", re.I)


@dataclass
class LineItem:
    index: int
    description: str
    code: str
    quantity: Optional[Decimal]
    unit_price: Optional[Decimal]
    amount: Optional[Decimal]
    unit: Optional[str] = None
    source: str = "TABLE"
    spans: list[Span] = field(default_factory=list)
    canonical: str = ""
    tokens: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.canonical:
            self.canonical = n.canonical(self.description, strip_number=False)
            self.tokens = n.tokens(self.canonical)

    def as_display(self) -> str:
        figures = []
        if self.quantity is not None:
            figures.append(f"qty {n.plain(self.quantity)}{(' ' + self.unit) if self.unit else ''}")
        if self.unit_price is not None:
            figures.append(f"@ {n.plain(self.unit_price)}")
        if self.amount is not None:
            figures.append(f"= {n.plain(self.amount)}")
        head = " ".join(x for x in (self.code, self.description) if x) or "(no description)"
        return f"{head}: {' '.join(figures)}" if figures else head


def _union(boxes: Sequence[tuple[float, float, float, float]]) -> Optional[tuple[float, float, float, float]]:
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def from_tables(tables: Sequence[Any]) -> list[LineItem]:
    """BODY rows of line-item tables (engine.OutTable, ARCH-44)."""
    out: list[LineItem] = []
    for table in tables:
        roles = {c.index: c.role for c in table.columns}
        desc = [i for i, r in roles.items() if r == "DESCRIPTION"]
        if not desc:
            text_cols = [c.index for c in table.columns if c.value_type == "TEXT"]
            desc = text_cols[:1]
        qty = [i for i, r in roles.items() if r == "QUANTITY"]
        price = [i for i, r in roles.items() if r == "UNIT_PRICE"]
        amount = [i for i, r in roles.items() if r in ("AMOUNT", "TOTAL")]
        code = [i for i, r in roles.items() if r in ("CODE", "REFERENCE")]
        unit = [i for i, r in roles.items() if r == "UNIT"]
        if not desc or not (qty or price or amount):
            continue
        cells = {(c.row, c.col): c for c in table.cells}
        for row in table.rows:
            if row.kind != "BODY" or row.index < table.header_rows:
                continue

            def cell(cols: list[int]):
                return next((cells[(row.index, c)] for c in cols if (row.index, c) in cells), None)

            d, q, p, a, k, u = cell(desc), cell(qty), cell(price), cell(amount), cell(code), cell(unit)
            if d is None and k is None:
                continue
            numbers = [x.number if x is not None and x.number is not None else None for x in (q, p, a)]
            if all(x is None for x in numbers):
                continue
            if numbers[0] is None and numbers[1] is None and d is not None and SUMMARY_ROW.search(d.text or ""):
                continue
            boxes = [x.bbox for x in (d, q, p, a, k, u) if x is not None and x.bbox is not None]
            bbox = _union(boxes) if getattr(table, "rotation", 0) == 0 else None
            text = " | ".join(x.text for x in (k, d, q, p, a) if x is not None and x.text)
            out.append(LineItem(len(out), d.text if d is not None else "", k.text.strip() if k is not None else "",
                                numbers[0], numbers[1], numbers[2], u.text if u is not None else None, "TABLE",
                                [Span(row.page, bbox, text)]))
    return out


def from_entities(fields: Mapping[str, Any], *, workspace_currency: Optional[str] = None) -> list[LineItem]:
    """Line items from extracted_entities through ARCH-31's reader."""
    from app.services.procurement_matching import line_extraction

    doc = line_extraction.extract_lines(fields, workspace_currency=workspace_currency)
    out: list[LineItem] = []
    for line in doc.lines:
        if not line.is_readable:
            continue
        micros = Decimal(1_000_000)
        out.append(LineItem(len(out), line.description, line.sku or "", line.quantity,
                            None if line.unit_price_micros is None else Decimal(line.unit_price_micros) / micros,
                            None if line.amount_micros is None else Decimal(line.amount_micros) / micros,
                            line.unit, "EXTRACTED", []))
    return out


def _close(a: Optional[Decimal], b: Optional[Decimal], money_tol: Decimal, rel_tol: Decimal) -> Optional[bool]:
    if a is None or b is None:
        return None
    diff = abs(a - b)
    if diff <= money_tol:
        return True
    top = max(abs(a), abs(b))
    return bool(rel_tol > 0 and top > 0 and diff / top <= rel_tol)


def pair_score(a: LineItem, b: LineItem, desc_sim: float, *, money_tol: Decimal, rel_tol: Decimal) -> float:
    agree = [x for x in (_close(getattr(a, k), getattr(b, k), money_tol, rel_tol) for k in ATTRIBUTES) if x is not None]
    numeric = (sum(agree) / len(agree)) if agree else 0.0
    if a.code and b.code:
        code_term = 1.0 if n.canonical(a.code, strip_number=False) == n.canonical(b.code, strip_number=False) else 0.0
        return 0.60 * desc_sim + 0.25 * code_term + 0.15 * numeric
    return 0.85 * desc_sim + 0.15 * numeric


@dataclass
class LineFinding:
    kind: str
    label: str
    materiality: float
    members: dict[int, Optional[LineItem]]
    detail: dict


def compare_group(members: dict[int, LineItem], participants: Sequence[int], totals: dict[int, Optional[Decimal]],
                  *, money_tol: Decimal, rel_tol: Decimal) -> list[LineFinding]:
    """Findings for one aligned group of rows (doc canonical index -> row)."""
    sample = members[min(members)]
    label = (sample.description or sample.code or "line")[:200]
    out: list[LineFinding] = []
    missing = [d for d in participants if d not in members]
    if missing:
        shares = []
        for d, item in members.items():
            total = totals.get(d)
            if item.amount is not None and total:
                shares.append(float(abs(item.amount) / abs(total)))
        score = (mat.LINE_MISSING_BASE + 0.3 * min(1.0, max(shares) / 0.1)) if shares else mat.LINE_MISSING_UNPRICED
        out.append(LineFinding(v.KIND_LINE_MISSING, label, score, {d: members.get(d) for d in participants},
                               {"missing_in": missing}))
        if len(members) < 2:
            return out
    differing: dict[str, list[list[int]]] = {}
    rels: list[float] = []
    for a, b in combinations(sorted(members), 2):
        x, y = members[a], members[b]
        for attr in ATTRIBUTES:
            ok = _close(getattr(x, attr), getattr(y, attr), money_tol, rel_tol)
            if ok is False:
                differing.setdefault(attr, []).append([a, b])
                rels.append(mat.relative(getattr(x, attr), getattr(y, attr)))
        if x.code and y.code and n.canonical(x.code, strip_number=False) != n.canonical(y.code, strip_number=False):
            differing.setdefault("code", []).append([a, b])
    if differing:
        numeric = [k for k in differing if k in ATTRIBUTES]
        if numeric:
            score = max(mat.scaled(mat.LINE_NUMERIC, r) for r in rels)
        else:
            score = mat.LINE_CODE
        out.append(LineFinding(v.KIND_LINE_MISMATCH, label, score, {d: members.get(d) for d in participants},
                               {"attributes": sorted(differing), "pairs": differing}))
    elif len({m.canonical for m in members.values()}) > 1:
        out.append(LineFinding(v.KIND_LINE_MISMATCH, label, mat.LINE_WORDING, {d: members.get(d) for d in participants},
                               {"attributes": ["description"]}))
    return out


__all__ = ["ATTRIBUTES", "SUMMARY_ROW", "LineFinding", "LineItem", "compare_group", "from_entities", "from_tables", "pair_score"]
