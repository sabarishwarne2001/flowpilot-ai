"""ARCH43-S1:features — per-page boundary signals. Pure; no I/O.

Every signal is computed from text the OCR stage already stored
(work_items.extraction_metadata["pages"]): nothing is re-OCR'd.

For page i (1-based, i >= 2) against page i-1:

  layout_change   1 - cosine of the ARCH-41 fingerprint of the two pages'
                  HEADER SKELETONS (first lines, digits masked). A new
                  document usually brings a new letterhead.
  page_one        page i says it is page 1 ("Page 1 of 3", "1/3", "- 1 -").
  continues       page i says page k and page i-1 said k-1.
  prev_last       page i-1 said "k of k": the previous document ended.
  prev_blank      page i-1 was blank (a separator or a duplex back side).
  blank           page i is blank (rarely the first page of anything).
  separator       page i-1 was a separator sheet.
  type_change     the page classifier's type changed (both confident).
  title           page i carries a title phrase in its first lines.
  number_change   a document number ("Invoice No", "PO No", ...) in the
                  header differs from the previous page's.
  number_same     the same document number continues.
  text_jump       |log length ratio| of the two pages, capped at 1.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from app.services.extraction_memory import fingerprint as fp
from app.services.packets import page_classifier as pc

FEATURES: tuple[str, ...] = (
    "layout_change", "page_one", "continues", "prev_last", "prev_blank", "blank", "separator",
    "type_change", "title", "number_change", "number_same", "text_jump",
)
HEADER_LINES = 10
BLANK_CHARS = 15

_PAGE_OF = re.compile(r"\bpage\s*(\d{1,3})\s*(?:of|/)\s*(\d{1,3})\b", re.I)
_PAGE = re.compile(r"\bpage\s*(\d{1,3})\b", re.I)
_SLASH = re.compile(r"^\s*(\d{1,3})\s*/\s*(\d{1,3})\s*$")
_DASH = re.compile(r"^\s*-\s*(\d{1,3})\s*-\s*$")
_SEPARATOR = re.compile(r"\b(separator\s+sheet|document\s+separator|patch\s*(?:code|t)|batch\s+separator|end\s+of\s+document)\b", re.I)
_NUMBER = re.compile(
    r"\b(invoice|inv|po|purchase\s+order|order|receipt|grn|statement|policy|claim|contract|lease|reference|ref|"
    r"document|doc|bill\s+of\s+lading|b/l|application|account|a/c)\s*(?:no|number|num|#|id)\.?\s*[:#.]?\s*"
    r"([A-Z0-9][A-Z0-9\-/]{2,})", re.I)


@dataclass
class PageFacts:
    text: str
    blank: bool
    number: Optional[tuple[int, Optional[int]]]
    separator: bool
    cls: pc.PageClass
    signature: Optional[list[float]]
    doc_numbers: dict[str, str] = field(default_factory=dict)
    length: int = 0


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def page_number(text: str) -> Optional[tuple[int, Optional[int]]]:
    lines = _lines(text)
    edge = lines[:3] + lines[-3:]
    for line in edge:
        m = _PAGE_OF.search(line)
        if m:
            k, n = int(m.group(1)), int(m.group(2))
            if 1 <= k <= n:
                return k, n
    for line in edge:
        m = _SLASH.match(line)
        if m and 1 <= int(m.group(1)) <= int(m.group(2)):
            return int(m.group(1)), int(m.group(2))
        m = _PAGE.search(line) or _DASH.match(line)
        if m and int(m.group(1)) >= 1:
            return int(m.group(1)), None
    return None


def _doc_numbers(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in _lines(text)[:15]:
        for m in _NUMBER.finditer(line):
            label = re.sub(r"\s+", " ", m.group(1).lower())
            found.setdefault(label, m.group(2).upper().strip("-/"))
    return found


def facts(text: str, extra_hints: Optional[Mapping[str, Sequence[str]]] = None) -> PageFacts:
    body = text or ""
    alnum = sum(ch.isalnum() for ch in body)
    header = "\n".join(_lines(body)[:HEADER_LINES])
    return PageFacts(
        text=body,
        blank=alnum < BLANK_CHARS,
        number=page_number(body),
        separator=bool(_SEPARATOR.search(body)) and alnum < 400,
        cls=pc.classify_page(body, extra_hints),
        signature=fp.signature(header),
        doc_numbers=_doc_numbers(body),
        length=alnum,
    )


def pair_features(prev: PageFacts, cur: PageFacts, prev_content: Optional[PageFacts] = None) -> dict[str, float]:
    """Features of the boundary BEFORE `cur`. `prev_content` is the last
    non-blank page before cur (layout and type compare against content, not a
    blank separator)."""
    ref = prev_content or prev
    layout = 0.0
    if cur.signature is not None and ref.signature is not None:
        layout = max(0.0, 1.0 - fp.cosine(cur.signature, ref.signature))
    elif not cur.blank and ref.signature is None:
        layout = 0.5
    continues = 0.0
    if cur.number and ref.number and cur.number[0] == ref.number[0] + 1:
        if cur.number[1] is None or ref.number[1] is None or cur.number[1] == ref.number[1]:
            continues = 1.0
    type_change = 0.0
    a, b = ref.cls, cur.cls
    if a.doc_type != pc.OTHER and b.doc_type != pc.OTHER and a.doc_type != b.doc_type:
        type_change = min(1.0, (a.confidence + b.confidence) / 2 + 0.25)
    shared = set(ref.doc_numbers) & set(cur.doc_numbers)
    number_change = 1.0 if any(ref.doc_numbers[k] != cur.doc_numbers[k] for k in shared) else 0.0
    number_same = 1.0 if any(ref.doc_numbers[k] == cur.doc_numbers[k] for k in shared) else 0.0
    jump = abs(math.log((cur.length + 1) / (ref.length + 1)))
    return {
        "layout_change": round(layout, 4),
        "page_one": 1.0 if cur.number and cur.number[0] == 1 else 0.0,
        "continues": continues,
        "prev_last": 1.0 if ref.number and ref.number[1] and ref.number[0] == ref.number[1] else 0.0,
        "prev_blank": 1.0 if prev.blank else 0.0,
        "blank": 1.0 if cur.blank else 0.0,
        "separator": 1.0 if prev.separator else 0.0,
        "type_change": round(type_change, 4),
        "title": 1.0 if cur.cls.title_hit else 0.0,
        "number_change": number_change,
        "number_same": number_same,
        "text_jump": round(min(1.0, jump / 3.0), 4),
    }


def packet_features(texts: Sequence[str], extra_hints=None) -> tuple[list[PageFacts], list[Optional[dict[str, float]]]]:
    """Facts per page and the boundary features before each page (None for page 1)."""
    pages = [facts(t, extra_hints) for t in texts]
    out: list[Optional[dict[str, float]]] = [None]
    last_content: Optional[PageFacts] = pages[0] if pages and not pages[0].blank else None
    for i in range(1, len(pages)):
        out.append(pair_features(pages[i - 1], pages[i], last_content))
        if not pages[i].blank and not pages[i].separator:
            last_content = pages[i]
    return pages, out


def vector(features: Mapping[str, float]) -> list[float]:
    return [float(features[name]) for name in FEATURES]


__all__ = ["FEATURES", "PageFacts", "facts", "packet_features", "page_number", "pair_features", "vector"]
