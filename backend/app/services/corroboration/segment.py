"""ARCH45-S1:segment — a document's text as clauses with geometry. Pure; no I/O.

Input is what the pipeline already stored: per page, the lines in reading
order with their boxes (for digital pages the text-layer lines, for scanned
pages the PaddleOCR blocks, both in 200-DPI page pixels, top-left origin), or
plain text when a document has no geometry. Nothing is re-OCR'd.

    lines --drop running headers/footers and page numbers--
          --drop lines inside an extracted table (the table layer compares those)--
          --break at clause numbers, headings, paragraph gaps; join across page breaks--
          --split very long clauses at sentence boundaries--> clauses

A clause keeps every line it came from (page, box, text): those are the
evidence spans a discrepancy points at and the viewer highlights.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from statistics import median
from typing import Optional, Sequence

from app.services.corroboration import normalize as n
from app.services.corroboration import vocabulary as v

Box = tuple[float, float, float, float]

_PAGE_NUMBER = re.compile(r"^(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?$|^[-–—]\s*\d{1,4}\s*[-–—]$", re.I)
_TERMINAL = re.compile(r"[.;:!?)\]]\s*$")
_SENTENCE = re.compile(r"(?<=[.;!?])\s+(?=[A-Z(\"'])")
_KEYWORD_HEADING = re.compile(r"^(article|section|schedule|annex(?:ure)?|appendix|part)\b", re.I)
#: "Invoice No: INV-8812", "Vendor: Acme Ltd": a short label and a value. These
#: are the fields layer's (extracted values, typed and normalised); as clauses
#: they would only restate it with less care.
_FIELD_LINE = re.compile(r"^[A-Za-z][A-Za-z .()/#&'-]{0,38}[A-Za-z.)#]\s*[:#]\s*\S.*$")


def is_field_line(text: str) -> bool:
    if not _FIELD_LINE.match(text) or len(text) > 100:
        return False
    label, _, value = text.partition(":") if ":" in text else text.partition("#")
    return len(label.split()) <= 5 and 0 < len(value.split()) <= 8


@dataclass(frozen=True)
class LineBox:
    text: str
    bbox: Optional[Box] = None
    #: True when a blank line (or equivalent) preceded this line: a paragraph break.
    para_break: bool = False


@dataclass(frozen=True)
class PageText:
    page: int
    width: float
    height: float
    lines: tuple[LineBox, ...]


@dataclass(frozen=True)
class Span:
    page: int
    bbox: Optional[Box]
    text: str

    def as_json(self) -> dict:
        out: dict = {"page": self.page, "text": self.text[:500]}
        if self.bbox is not None:
            out["bbox"] = {"x0": round(self.bbox[0], 1), "y0": round(self.bbox[1], 1),
                           "x1": round(self.bbox[2], 1), "y1": round(self.bbox[3], 1)}
        return out


@dataclass
class Clause:
    index: int
    number: Optional[str]
    section: str
    text: str
    canonical: str
    tokens: list[str]
    spans: list[Span] = field(default_factory=list)

    @property
    def page(self) -> int:
        return self.spans[0].page if self.spans else 1

    @property
    def display(self) -> str:
        return self.text


def _shape(text: str) -> str:
    return re.sub(r"\d+", "#", n.fold(text).lower())


def _in_band(line: LineBox, page: PageText, index: int, count: int) -> bool:
    if line.bbox is not None and page.height > 0:
        return line.bbox[1] < v.RUNNING_BAND * page.height or line.bbox[3] > (1 - v.RUNNING_BAND) * page.height
    return index < 2 or index >= count - 2


def drop_running(pages: Sequence[PageText]) -> list[list[LineBox]]:
    """Each page's lines without running headers/footers and page numbers."""
    kept: list[list[LineBox]] = []
    counts: dict[str, set[int]] = {}
    for page in pages:
        for i, line in enumerate(page.lines):
            if _in_band(line, page, i, len(page.lines)):
                counts.setdefault(_shape(line.text), set()).add(page.page)
    need = max(2, math.ceil(v.RUNNING_LINE_SHARE * len(pages)))
    running = {shape for shape, on in counts.items() if len(pages) >= 2 and len(on) >= need}
    for page in pages:
        out = []
        for i, line in enumerate(page.lines):
            text = n.fold(line.text)
            if not text:
                continue
            band = _in_band(line, page, i, len(page.lines))
            if band and (_shape(text) in running or _PAGE_NUMBER.match(text)):
                continue
            if _in_margin(line, page):
                continue
            out.append(line)
        kept.append(out)
    return kept


def _in_margin(line: LineBox, page: PageText) -> bool:
    """The outermost strip of the page: running heads and folios, even on a
    one-page document where nothing can recur."""
    if line.bbox is None or page.height <= 0:
        return False
    return line.bbox[3] < v.MARGIN_BAND * page.height or line.bbox[1] > (1 - v.MARGIN_BAND) * page.height


def reading_order(lines: Sequence[LineBox]) -> list[LineBox]:
    """Top to bottom, then left to right, when every line has a box; the
    stored order otherwise. A text layer lists runs in content-stream order,
    which need not be the order a person reads."""
    if not lines or any(ln.bbox is None for ln in lines):
        return list(lines)
    heights = sorted(ln.bbox[3] - ln.bbox[1] for ln in lines)
    tol = max(1.0, 0.5 * heights[len(heights) // 2])
    ordered = sorted(lines, key=lambda ln: (ln.bbox[1], ln.bbox[0]))
    rows: list[list[LineBox]] = []
    for ln in ordered:
        if rows and abs(ln.bbox[1] - rows[-1][0].bbox[1]) <= tol:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    return [ln for row in rows for ln in sorted(row, key=lambda x: x.bbox[0])]


def _inside(bbox: Optional[Box], regions: Sequence[Box]) -> bool:
    if bbox is None:
        return False
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return any(r[0] - 2 <= cx <= r[2] + 2 and r[1] - 2 <= cy <= r[3] + 2 for r in regions)


_HEADING_END = re.compile(r"[.;!?]\s*$")
_SENTENCE_END = re.compile(r"[.;:!?]\s*$")


def _is_heading(text: str) -> bool:
    words = text.split()
    if not words or len(words) > 10 or _HEADING_END.search(text):
        return False
    number, rest = n.strip_clause_number(text)
    body = rest.strip().rstrip(":")
    letters = [c for c in body if c.isalpha()]
    if not letters:
        return False
    if _KEYWORD_HEADING.match(text):
        return True
    upper = sum(1 for c in letters if c.isupper()) / len(letters)
    if upper >= 0.9 and len(letters) >= 3:
        return True
    # "5. Payment Terms": a number and a short title-cased label.
    return number is not None and len(body.split()) <= 6 and all(w[:1].isupper() or not w[:1].isalpha()
                                                                 for w in body.split() if len(w) > 3)


def _join(parts: Sequence[str]) -> str:
    out = ""
    for part in parts:
        part = n.fold(part)
        if not part:
            continue
        if out.endswith("-") and part[:1].islower() and len(out) > 1 and out[-2].isalpha():
            out = out[:-1] + part
        else:
            out = f"{out} {part}" if out else part
    return out


@dataclass
class _Open:
    number: Optional[str]
    section: str
    heading: Optional[str]
    lines: list[tuple[int, LineBox]] = field(default_factory=list)


def segment(pages: Sequence[PageText], *, table_regions: Optional[dict[int, list[Box]]] = None,
            date_order: str = "DMY") -> list[Clause]:
    """The document's clauses, in reading order."""
    table_regions = table_regions or {}
    pages = [PageText(p.page, p.width, p.height, tuple(reading_order(p.lines))) for p in sorted(pages, key=lambda p: p.page)]
    kept = drop_running(pages)
    heights = [ln.bbox[3] - ln.bbox[1] for lines in kept for ln in lines if ln.bbox is not None and ln.bbox[3] > ln.bbox[1]]
    h = median(heights) if heights else 0.0
    clauses: list[_Open] = []
    section = ""
    current: Optional[_Open] = None
    last_box: Optional[Box] = None
    last_page = 0

    def close() -> None:
        nonlocal current
        if current is not None and current.lines:
            clauses.append(current)
        current = None

    for page, lines in zip(pages, kept):
        regions = table_regions.get(page.page, [])
        for line in lines:
            if _inside(line.bbox, regions):
                continue
            text = n.fold(line.text)
            if is_field_line(text):
                continue
            number, _rest = n.strip_clause_number(text)
            heading = _is_heading(text)
            if current is not None and current.lines and (number is not None or heading) \
                    and not (current.heading is not None and len(current.lines) == 1) \
                    and not _SENTENCE_END.search(n.fold(current.lines[-1][1].text)):
                # "... within thirty / (30) days": a wrapped sentence, not a new clause.
                number, heading = None, False
                current.lines.append((page.page, line))
                last_box, last_page = line.bbox, page.page
                continue
            new_page = page.page != last_page
            gap_break = False
            if line.para_break:
                gap_break = True
            elif not new_page and line.bbox is not None and last_box is not None and h > 0:
                gap = line.bbox[1] - last_box[3]
                gap_break = gap > 0.9 * h or gap < -0.5 * h
            if heading:
                close()
                section = n.strip_clause_number(text)[1].strip().rstrip(":") or text
                current = _Open(number, section, text)
                current.lines.append((page.page, line))
            elif number is not None:
                if current is not None and current.heading is not None and len(current.lines) == 1:
                    # the heading line opened this clause; a numbered sub-clause starts a new one
                    clauses.append(current)
                    current = None
                close()
                current = _Open(number, section, None)
                current.lines.append((page.page, line))
            elif current is None:
                current = _Open(None, section, None)
                current.lines.append((page.page, line))
            elif new_page:
                previous = current.lines[-1][1].text if current.lines else ""
                if _TERMINAL.search(n.fold(previous)) and not text[:1].islower():
                    close()
                    current = _Open(None, section, None)
                current.lines.append((page.page, line))
            elif gap_break and not (current.heading is not None and len(current.lines) == 1):
                close()
                current = _Open(None, section, None)
                current.lines.append((page.page, line))
            else:
                current.lines.append((page.page, line))
            last_box, last_page = line.bbox, page.page
    close()

    out: list[Clause] = []
    for open_ in clauses:
        # A heading alone (its sub-clauses follow as their own clauses) is a
        # section label, not a clause.
        if open_.heading is not None and len(open_.lines) == 1:
            continue
        spans = [Span(p, ln.bbox, n.fold(ln.text)) for p, ln in open_.lines]
        text = _join([s.text for s in spans])
        for k, (piece_spans, piece) in enumerate(_split_long(spans, text)):
            canon = n.canonical(piece, date_order=date_order)
            if not canon:
                continue
            out.append(Clause(len(out), open_.number if k == 0 else None, open_.section, piece, canon,
                              n.tokens(canon), list(piece_spans)))
        if len(out) >= v.MAX_CLAUSES_PER_DOCUMENT:
            break
    return out


def _split_long(spans: list[Span], text: str) -> list[tuple[list[Span], str]]:
    if len(text) <= v.MAX_CLAUSE_CHARS:
        return [(spans, text)]
    sentences = _SENTENCE.split(text)
    pieces: list[str] = []
    buf = ""
    for s in sentences:
        if buf and len(buf) + 1 + len(s) > v.MAX_CLAUSE_CHARS // 2:
            pieces.append(buf)
            buf = s
        else:
            buf = f"{buf} {s}".strip()
    if buf:
        pieces.append(buf)
    out: list[tuple[list[Span], str]] = []
    cursor = 0
    for piece in pieces:
        # the spans whose text overlaps this piece (by running character count)
        start, end = cursor, cursor + len(piece)
        chosen, offset = [], 0
        for s in spans:
            s_start, s_end = offset, offset + len(s.text)
            if s_end > start and s_start < end:
                chosen.append(s)
            offset = s_end + 1
        out.append((chosen or spans[:1], piece))
        cursor = end + 1
    return out


def pages_from_text(text: str) -> list[PageText]:
    """A document without stored geometry: form feeds split pages, blank lines split paragraphs."""
    pages = (text or "").split("\f")
    out = []
    for i, page_text in enumerate(pages, start=1):
        lines: list[LineBox] = []
        blank = False
        for raw in page_text.splitlines():
            if not raw.strip():
                blank = True
                continue
            lines.append(LineBox(raw, None, para_break=blank and bool(lines)))
            blank = False
        out.append(PageText(i, 0.0, 0.0, tuple(lines)))
    return out


def pages_from_metadata(pages: Sequence[dict], fallback_text: str = "") -> list[PageText]:
    """work_items.extraction_metadata["pages"] -> PageText (blocks, else the page text)."""
    out: list[PageText] = []
    for raw in pages or []:
        number = int(raw.get("page_number") or len(out) + 1)
        width, height = float(raw.get("width") or 0), float(raw.get("height") or 0)
        blocks = raw.get("blocks") or []
        lines: list[LineBox] = []
        if blocks:
            for b in blocks:
                box = b.get("box") or {}
                bbox = None
                if all(k in box for k in ("x0", "y0", "x1", "y1")):
                    bbox = (float(box["x0"]), float(box["y0"]), float(box["x1"]), float(box["y1"]))
                if (b.get("text") or "").strip():
                    lines.append(LineBox(str(b.get("text")), bbox))
        else:
            for sub in pages_from_text(str(raw.get("text") or "")):
                lines.extend(sub.lines)
        out.append(PageText(number, width, height, tuple(lines)))
    if not out and fallback_text:
        return pages_from_text(fallback_text)
    return out


__all__ = ["Box", "Clause", "LineBox", "PageText", "Span", "drop_running", "is_field_line", "pages_from_metadata",
           "pages_from_text", "segment"]
