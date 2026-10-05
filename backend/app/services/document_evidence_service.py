"""PHASE 4 — where on the page a value was read: evidence for reviewers.

The review hub shows a reviewer the page with the disputed value highlighted,
so "the two agents disagree on the invoice number" becomes "here is what the
page says". Nothing is re-read: the line boxes the extraction stored
(`work_items.extraction_metadata["pages"][i]["blocks"]`, from the PDF text
layer or OCR) are searched for each value.

Matching is loose on purpose - case, spaces and thousands separators are
ignored, so "1000.00" finds "1,000.00" - and the box is narrowed from the
whole line to the matched characters by their position in the line. A value
the page does not print in a comparable form (a date reformatted by the
model, say) is reported as not found rather than guessed.

Boxes are returned normalised to the page (0..1, top-left origin), so the
console can draw them over a page image of any size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

MAX_LOCATIONS_PER_VALUE = 3
MIN_VALUE_CHARS = 2
_IGNORED = {" ", ",", "\t", "\n"}


@dataclass(frozen=True)
class Location:
    field: Optional[str]
    value: str
    source: str  # "extracted" | "candidate"
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Evidence:
    pages: list[dict[str, Any]] = field(default_factory=list)
    locations: list[Location] = field(default_factory=list)
    not_found: list[dict[str, Any]] = field(default_factory=list)


def _loose(text: str) -> tuple[str, list[int]]:
    """The comparable form of `text` and, for each of its characters, the index in `text`."""
    chars: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        if char in _IGNORED:
            continue
        chars.append(char.lower())
        positions.append(index)
    return "".join(chars), positions


def _scalar_values(entities: Any) -> Iterable[tuple[str, str]]:
    if not isinstance(entities, dict):
        return
    for key, value in entities.items():
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, (str, int, float)) and not isinstance(item, bool):
                    yield str(key), str(item)
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            yield str(key), str(value)


def _find(pages: list[dict[str, Any]], value: str) -> list[tuple[int, float, float, float, float]]:
    needle, _ = _loose(value)
    if len(needle) < MIN_VALUE_CHARS:
        return []
    found: list[tuple[int, float, float, float, float]] = []
    for page in pages:
        width, height = float(page.get("width") or 0), float(page.get("height") or 0)
        number = int(page.get("page_number") or 0)
        if width <= 0 or height <= 0 or number < 1:
            continue
        for block in page.get("blocks") or []:
            box = block.get("box")
            text = str(block.get("text") or "")
            if not box or not text:
                continue
            haystack, positions = _loose(text)
            start = haystack.find(needle)
            if start < 0:
                continue
            first, last = positions[start], positions[start + len(needle) - 1] + 1
            x0, x1 = float(box["x0"]), float(box["x1"])
            span = max(x1 - x0, 1.0)
            left = x0 + span * first / len(text)
            right = x0 + span * last / len(text)
            found.append((number, left / width, float(box["y0"]) / height, right / width, float(box["y1"]) / height))
            if len(found) >= MAX_LOCATIONS_PER_VALUE:
                return found
    return found


def locate(
    extraction_metadata: Any,
    extracted_entities: Any,
    *,
    candidates: Iterable[tuple[Optional[str], str]] = (),
) -> Evidence:
    """Locate every extracted value, and every candidate value, on the stored pages."""
    stored = (extraction_metadata or {}).get("pages") if isinstance(extraction_metadata, dict) else None
    pages = [p for p in (stored or []) if isinstance(p, dict)]
    evidence = Evidence(pages=[
        {"page_number": int(p.get("page_number") or 0), "width": p.get("width"), "height": p.get("height")}
        for p in pages
    ])
    wanted: list[tuple[Optional[str], str, str]] = [
        (key, value, "extracted") for key, value in _scalar_values(extracted_entities)
    ]
    wanted += [(key, str(value), "candidate") for key, value in candidates if str(value).strip()]
    seen: set[tuple[Optional[str], str, str]] = set()
    for key, value, source in wanted:
        if (key, value, source) in seen:
            continue
        seen.add((key, value, source))
        hits = _find(pages, value)
        if not hits:
            evidence.not_found.append({"field": key, "value": value, "source": source})
            continue
        for page, x0, y0, x1, y1 in hits:
            evidence.locations.append(Location(key, value, source, page, round(x0, 4), round(y0, 4),
                                               round(x1, 4), round(y1, 4)))
    return evidence


__all__ = ["Evidence", "Location", "locate"]
