"""ARCH-32 — detection. Pure: page text and block geometry in, candidates out.

NO SESSION, NO STORAGE, NO CLOCK
================================

`detect_candidates` takes plain data structures and returns plain data
structures. Everything that talks to the database lives in
`redaction_service.py`. That split is what lets `verify_arch32.py` drive the
sub-block geometry gates with hand-written block boxes instead of a migrated
corpus, and it is what keeps the candidate's PLAINTEXT — which every candidate
carries — out of anything that could persist it by accident.

A `Candidate` holds `text`. That is deliberate and it is the reason this
module's output must never be handed to an ORM. `redaction_service` converts
candidates to rows and, in doing so, replaces `text` with its HMAC digest.
The plaintext then exists only for the life of the worker call.

HOW A CHARACTER SPAN BECOMES A RECTANGLE
========================================

ARCH-11 stores, per page, the page text and a list of OCR blocks, each with
its own text and a pixel box. `DocumentPage.block_spans()` maps each block
onto its character span in the page text — the invariant ARCH-11 Step 3
established (`content == page_text[start:end]`).

Given a match at `[match_start, match_end)` in the page text:

  1. Find the block whose span CONTAINS the match. If the match straddles two
     blocks, take the union of their boxes and drop to BLOCK precision — a
     value split across two OCR lines has no single rectangle, and guessing
     one is how a redaction lands half on the wrong row.
  2. Within that block, compute the proportional offset of the match inside
     the block's own text and narrow the box horizontally. This is GLYPH
     precision.
  3. If there are no blocks, or the block spans could not be built, there is
     nothing to narrow within. The whole page is not a rectangle worth
     burning, so the candidate is DROPPED rather than widened to the page.

THE PROPORTIONAL OFFSET IS AN APPROXIMATION, AND IT IS STATED AS ONE
====================================================================

Characters are not equal width. `1` and `W` occupy different advances in every
proportional font, so a box computed by character ratio is right to within a
few percent of the block width, not exact. Three things make that safe:

  * The box is padded before burning (`BOX_PADDING_POINTS`).
  * The narrowed box is never allowed to be narrower than the proportional
    estimate by rounding — `_narrow` expands both edges by one character's
    average width.
  * A candidate whose narrowed box would exceed 85% of the block is widened to
    the block outright and marked BLOCK, because at that point the narrowing
    has bought nothing and the honest label is the useful one.

WHY BLOCK PRECISION IS A FIRST-CLASS OUTCOME AND NOT A FAILURE
==============================================================

§3.3 requires it: "a candidate whose box cannot be resolved below block level
is widened to the whole block and marked geometry_precision = 'BLOCK' so the
reviewer sees it". Widening redacts MORE than the match — a whole line rather
than a number — which is the correct direction for a safety tool and the wrong
direction for a document's readability. The reviewer is the one who decides
whether that trade is acceptable on this page, which they can only do if the
studio tells them. Hence a column, not a silent fallback.

COORDINATE SPACES
=================

OCR block boxes are in PIXELS of the rendered page, with the origin at the
TOP-left. Regions are stored in PDF POINTS with the origin at the BOTTOM-left.
The conversion needs the page's pixel dimensions (from `extraction_metadata`)
and its point dimensions (from the PDF itself). When the pixel dimensions are
missing — some older extraction rows have `width: null` — the candidate is
dropped rather than converted against a guessed DPI. A rectangle placed by
guesswork is worse than no rectangle, because the reviewer sees a box and
assumes the value under it is covered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from app.services.redaction import validators
from app.services.redaction.vocabulary import (
    CHECKSUM_DETECTORS,
    DETECTOR_AADHAAR,
    DETECTOR_CARD_NUMBER,
    DETECTOR_DATE_OF_BIRTH,
    DETECTOR_EMAIL,
    DETECTOR_GSTIN,
    DETECTOR_IBAN,
    DETECTOR_KNOWN_PARTY,
    DETECTOR_PAN_INDIA,
    DETECTOR_PHONE,
    DETECTOR_US_SSN,
    PRECISION_BLOCK,
    PRECISION_GLYPH,
    profile_detectors,
)

__all__ = [
    "Candidate",
    "PageGeometry",
    "DetectionResult",
    "detect_candidates",
    "CONFIDENCE_CHECKSUM",
    "CONFIDENCE_CONTEXT",
    "CONFIDENCE_PATTERN",
    "MAX_NARROW_RATIO",
]

# ---------------------------------------------------------------------------
# Confidence
#
# Three levels, not a continuum. A number between them would imply a
# calibration this phase has not done, and the studio renders the difference
# as a pill the reviewer reads — "checksum valid" is a claim the arithmetic
# supports, and "0.62" is a claim nothing supports.
# ---------------------------------------------------------------------------

#: The issuer's own arithmetic agrees. §3.9's "very high precision" claim.
CONFIDENCE_CHECKSUM: float = 0.99
#: Pattern matched and a context word is nearby. Good, not provable.
CONFIDENCE_CONTEXT: float = 0.75
#: Pattern matched and nothing corroborates it. Enabled, but the reviewer is
#: the one deciding — and the studio sorts these to the top of the list.
CONFIDENCE_PATTERN: float = 0.40

#: Above this share of the block's width, narrowing has bought nothing worth
#: the precision claim. See the header.
MAX_NARROW_RATIO: float = 0.85


@dataclass(frozen=True)
class PageGeometry:
    """What is needed to place a pixel box into PDF point space."""

    page_number: int
    width_points: float
    height_points: float
    width_px: Optional[int]
    height_px: Optional[int]

    @property
    def is_convertible(self) -> bool:
        return bool(
            self.width_px
            and self.height_px
            and self.width_points > 0
            and self.height_points > 0
        )


@dataclass(frozen=True)
class Candidate:
    """One detection. CARRIES PLAINTEXT — never hand this to an ORM."""

    page_number: int
    x0: float
    y0: float
    x1: float
    y1: float
    detector: str
    confidence: float
    geometry_precision: str
    text: str
    #: Character span in the page text. Kept for the gates and for debugging
    #: a mis-placed box; never stored.
    char_start: int = 0
    char_end: int = 0

    @property
    def checksum_validated(self) -> bool:
        return self.detector in CHECKSUM_DETECTORS


@dataclass
class DetectionResult:
    candidates: list[Candidate] = field(default_factory=list)
    #: Matches that were found in the text and could not be placed on the
    #: page. Counted and surfaced, never silently discarded: a page whose
    #: geometry is unusable is a page the reviewer must be told about, or
    #: they approve a job that quietly redacted nothing on it.
    unplaced: int = 0
    #: Pages that carried no usable block geometry at all.
    pages_without_geometry: list[int] = field(default_factory=list)

    def by_detector(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for candidate in self.candidates:
            counts[candidate.detector] = counts.get(candidate.detector, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Patterns
#
# Every pattern is deliberately LOOSE. Tightening a pattern to reduce false
# positives moves the decision from the validator (which is arithmetic and
# provable) into the regex (which is a guess), and a value this engine never
# proposes is a value no reviewer can enable.
# ---------------------------------------------------------------------------

_PATTERNS: dict[str, re.Pattern[str]] = {
    DETECTOR_CARD_NUMBER: re.compile(r"\b(?:\d[ \-]?){12,18}\d\b"),
    DETECTOR_AADHAAR: re.compile(r"\b\d{4}[ \-]?\d{4}[ \-]?\d{4}\b"),
    DETECTOR_PAN_INDIA: re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
    DETECTOR_GSTIN: re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]\b"),
    DETECTOR_US_SSN: re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    DETECTOR_IBAN: re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}[ ]?[A-Z0-9]{1,4}\b"),
    DETECTOR_EMAIL: re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    DETECTOR_PHONE: re.compile(
        r"(?<![\d.])(?:\+\d{1,3}[ \-]?)?(?:\(\d{2,4}\)[ \-]?)?\d{3,5}[ \-]?\d{3,4}[ \-]?\d{0,4}(?![\d.])"
    ),
    DETECTOR_DATE_OF_BIRTH: re.compile(
        r"\b(?:\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}"
        r"|\d{4}-\d{2}-\d{2}"
        r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{2,4})\b",
        re.IGNORECASE,
    ),
}

#: Words that, within `_CONTEXT_WINDOW` characters before a match, raise a
#: pattern-only detector from CONFIDENCE_PATTERN to CONFIDENCE_CONTEXT.
_CONTEXT_WORDS: dict[str, tuple[str, ...]] = {
    DETECTOR_PHONE: ("phone", "mobile", "tel", "telephone", "contact", "cell", "fax"),
    DETECTOR_EMAIL: ("email", "e-mail", "mail", "contact"),
    DETECTOR_DATE_OF_BIRTH: ("dob", "date of birth", "born", "birth date", "birthdate"),
}

#: `date_of_birth` REQUIRES context. Dates are the most common string on a
#: commercial document — invoice date, due date, delivery date — and a
#: detector that proposed every one of them would make the region list
#: unreadable and train the reviewer to approve without looking.
_CONTEXT_REQUIRED: frozenset[str] = frozenset({DETECTOR_DATE_OF_BIRTH})

_CONTEXT_WINDOW: int = 40

#: Detection order, most specific first. NOT the profile's declaration order.
#:
#: Found by a gate, not by review. `hipaa_safe_harbor` lists `phone` before
#: `card_number`, and running in that order let the loose phone pattern claim
#: `4111 1111 1111` out of the middle of a card number — so the card was never
#: proposed, the reviewer saw a low-confidence "phone" box covering three
#: quarters of it, and the last four digits stayed on the page.
#:
#: Specificity here means "how much of the match is confirmed by something
#: other than the shape": checksum families first, then patterns with
#: structural anchors (`@`, a date separator), then the two that are really
#: just digit runs.
_DETECTOR_ORDER: tuple[str, ...] = (
    DETECTOR_GSTIN,        # contains a PAN; must claim before it
    DETECTOR_IBAN,
    DETECTOR_AADHAAR,
    DETECTOR_CARD_NUMBER,  # contains shorter digit runs; must claim before phone
    DETECTOR_US_SSN,
    DETECTOR_PAN_INDIA,
    DETECTOR_EMAIL,
    DETECTOR_DATE_OF_BIRTH,
    DETECTOR_PHONE,
)


def _ordered(detectors: tuple[str, ...]) -> list[str]:
    """The profile's detectors, in specificity order. Unknown ones go last."""
    known = [d for d in _DETECTOR_ORDER if d in detectors]
    rest = [d for d in detectors if d not in _DETECTOR_ORDER]
    return known + rest


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _pixel_box_to_points(
    box: dict[str, Any], geometry: PageGeometry
) -> Optional[tuple[float, float, float, float]]:
    """Top-left pixel box -> bottom-left point box. None if unconvertible."""
    if not geometry.is_convertible:
        return None
    try:
        px0 = float(box["x0"])
        py0 = float(box["y0"])
        px1 = float(box["x1"])
        py1 = float(box["y1"])
    except (KeyError, TypeError, ValueError):
        return None

    scale_x = geometry.width_points / float(geometry.width_px or 1)
    scale_y = geometry.height_points / float(geometry.height_px or 1)

    x0 = min(px0, px1) * scale_x
    x1 = max(px0, px1) * scale_x
    # Pixel y grows downward; point y grows upward.
    y1 = geometry.height_points - (min(py0, py1) * scale_y)
    y0 = geometry.height_points - (max(py0, py1) * scale_y)

    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _narrow(
    block_box: tuple[float, float, float, float],
    block_text: str,
    start_in_block: int,
    end_in_block: int,
) -> Optional[tuple[float, float, float, float]]:
    """Narrow a block box horizontally by proportional character offset.

    Returns None when narrowing is not worth claiming GLYPH precision for —
    an empty block text, or a match that covers most of the block anyway.
    """
    length = len(block_text)
    if length <= 0:
        return None

    x0, y0, x1, y1 = block_box
    width = x1 - x0
    if width <= 0:
        return None

    char_width = width / length
    # One character of slack on each side. Characters are not equal width, so
    # the proportional estimate is right to a few percent; the slack absorbs
    # that, and the burn padding absorbs the rest.
    left = x0 + max(0.0, (start_in_block - 1)) * char_width
    right = x0 + min(float(length), (end_in_block + 1)) * char_width

    if right <= left:
        return None
    if (right - left) / width > MAX_NARROW_RATIO:
        return None
    return (left, y0, right, y1)


def _union(
    boxes: Sequence[tuple[float, float, float, float]]
) -> tuple[float, float, float, float]:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _has_context(page_text: str, start: int, detector: str) -> bool:
    words = _CONTEXT_WORDS.get(detector)
    if not words:
        return False
    window = page_text[max(0, start - _CONTEXT_WINDOW) : start].casefold()
    return any(word in window for word in words)


def _confidence_for(page_text: str, start: int, detector: str) -> Optional[float]:
    """None means 'do not propose this candidate at all'."""
    if detector in CHECKSUM_DETECTORS:
        return CONFIDENCE_CHECKSUM
    if _has_context(page_text, start, detector):
        return CONFIDENCE_CONTEXT
    if detector in _CONTEXT_REQUIRED:
        return None
    return CONFIDENCE_PATTERN


def _known_party_spans(
    page_text: str, parties: Iterable[str]
) -> list[tuple[int, int, str]]:
    """Exact and token-order-insensitive matches for tenant-supplied names.

    §3.9's honest limit lives here: without a model, a personal name is found
    only when the document's own extracted parties or a tenant list already
    names it. "Sarah" in a sentence nobody extracted is invisible to this
    function, and the studio says so on every profile that includes names.
    """
    found: list[tuple[int, int, str]] = []
    folded_page = page_text.casefold()
    seen: set[tuple[int, int]] = set()

    for party in parties:
        name = (party or "").strip()
        if len(name) < 3:
            continue

        # Exact, case-insensitive.
        needle = name.casefold()
        cursor = 0
        while True:
            index = folded_page.find(needle, cursor)
            if index < 0:
                break
            span = (index, index + len(needle))
            if span not in seen:
                seen.add(span)
                found.append((span[0], span[1], page_text[span[0] : span[1]]))
            cursor = index + max(1, len(needle))

        # Token-order-insensitive: "Kapoor, Priya" for "Priya Kapoor". Only
        # attempted for two-token names, because three tokens produce enough
        # permutations to start matching unrelated prose.
        tokens = [t for t in re.split(r"[\s,]+", name) if t]
        if len(tokens) == 2:
            swapped = f"{tokens[1]} {tokens[0]}".casefold()
            index = folded_page.find(swapped)
            while index >= 0:
                span = (index, index + len(swapped))
                if span not in seen:
                    seen.add(span)
                    found.append((span[0], span[1], page_text[span[0] : span[1]]))
                index = folded_page.find(swapped, index + max(1, len(swapped)))

    return found


def _validated(detector: str, text: str) -> bool:
    validator = validators.validator_for(detector)
    if validator is None:
        return True  # pattern-only detector; confidence carries the doubt
    return validator(text)


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def detect_candidates(
    *,
    pages: Sequence[Any],
    geometries: Sequence[PageGeometry],
    profile_key: str,
    known_parties: Sequence[str] = (),
) -> DetectionResult:
    """Run a profile's detectors over extracted pages and place each match.

    `pages` are `app.services.document_models.DocumentPage` instances (or
    anything with `page_number`, `text` and `block_spans()`). They are passed
    in rather than loaded here so this function stays pure and so the gates
    can hand it three lines of hand-written geometry.
    """
    detectors = profile_detectors(profile_key)
    geometry_by_page = {g.page_number: g for g in geometries}
    result = DetectionResult()

    for page in pages:
        page_number = int(getattr(page, "page_number", 0) or 0)
        page_text: str = getattr(page, "text", "") or ""
        geometry = geometry_by_page.get(page_number)

        if not page_text or geometry is None or not geometry.is_convertible:
            if page_text:
                result.pages_without_geometry.append(page_number)
                result.unplaced += 1
            continue

        spans, _source = page.block_spans()
        if not spans:
            result.pages_without_geometry.append(page_number)
            continue

        # Pre-convert every block box once. A page with two hundred blocks and
        # forty matches would otherwise do eight thousand conversions.
        block_boxes: list[Optional[tuple[float, float, float, float]]] = []
        for span in spans:
            raw = getattr(span, "box", None)
            block_boxes.append(
                _pixel_box_to_points(raw, geometry) if isinstance(raw, dict) else None
            )

        claimed: list[tuple[int, int]] = []

        def emit(start: int, end: int, text: str, detector: str, confidence: float) -> None:
            """Place one match and record it, or count it as unplaced."""
            covering = [
                (index, span)
                for index, span in enumerate(spans)
                if _overlaps((start, end), (span.start, span.end))
            ]
            boxes = [
                block_boxes[index]
                for index, _ in covering
                if block_boxes[index] is not None
            ]
            if not boxes:
                result.unplaced += 1
                return

            if len(covering) == 1:
                index, span = covering[0]
                block_box = block_boxes[index]
                assert block_box is not None
                narrowed = _narrow(
                    block_box,
                    span.text,
                    max(0, start - span.start),
                    min(len(span.text), end - span.start),
                )
                if narrowed is not None:
                    box, precision = narrowed, PRECISION_GLYPH
                else:
                    box, precision = block_box, PRECISION_BLOCK
            else:
                # Straddles two or more OCR blocks. There is no single
                # rectangle for it; the union of the blocks is the honest
                # answer and BLOCK is the honest label.
                box, precision = _union(boxes), PRECISION_BLOCK

            result.candidates.append(
                Candidate(
                    page_number=page_number,
                    x0=box[0],
                    y0=box[1],
                    x1=box[2],
                    y1=box[3],
                    detector=detector,
                    confidence=confidence,
                    geometry_precision=precision,
                    text=text,
                    char_start=start,
                    char_end=end,
                )
            )
            claimed.append((start, end))

        # --- pattern detectors, most specific first ------------------------
        for detector in _ordered(detectors):
            pattern = _PATTERNS.get(detector)
            if pattern is None:
                continue
            for match in pattern.finditer(page_text):
                start, end = match.span()
                text = match.group(0)
                if not _validated(detector, text):
                    continue
                confidence = _confidence_for(page_text, start, detector)
                if confidence is None:
                    continue
                # A GSTIN contains a PAN and a card number contains shorter
                # digit runs. First detector to claim a span wins, which is
                # only correct because `_ordered` runs the specific ones
                # first -- see its comment for what happened when it did not.
                if any(_overlaps((start, end), c) for c in claimed):
                    continue
                emit(start, end, text, detector, confidence)

        # --- known parties -------------------------------------------------
        if DETECTOR_KNOWN_PARTY in detectors and known_parties:
            for start, end, text in _known_party_spans(page_text, known_parties):
                if any(_overlaps((start, end), c) for c in claimed):
                    continue
                emit(start, end, text, DETECTOR_KNOWN_PARTY, CONFIDENCE_CONTEXT)

    result.candidates.sort(
        key=lambda c: (c.page_number, -c.confidence, c.x0, c.y0, c.detector)
    )
    return result