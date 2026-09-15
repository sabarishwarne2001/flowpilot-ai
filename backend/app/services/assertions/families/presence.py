"""ARCH-33 — `presence`: the document must contain a named clause.

    The contract includes a data processing clause
      -> exists(clause: data processing)

HOW A HIT BECOMES A CONFIDENCE
==============================

One matched phrasing is a mention. "Personal data" appears in the recitals of
contracts that have no data-processing clause at all. Several DIFFERENT
phrasings matching in the same document is a clause, because the vocabulary of
a real DPA is wide and the vocabulary of a passing reference is one word.

So confidence is a function of how many DISTINCT patterns matched, not of how
many times any one of them did — a contract that says "Confidential
Information" forty times has said one thing forty times.

WHY AN EMPTY READING SET IS A FAIL AND NOT AN UNDETERMINED
==========================================================

`presence` answers "is this clause here". Nothing found is a real answer to
that question, and it is the answer the rule was written to catch. Returning
UNDETERMINED would route every compliant-looking contract to triage alongside
every non-compliant one and give the reviewer no signal.

It is a LOW-CONFIDENCE fail, because retrieval returning nothing and the
clause genuinely being absent look identical from here — the same asymmetry
`absence` carries in the opposite direction. The confidence the evaluation
reports for an empty set is what stops that fail from being acted on
automatically.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.services.assertions import vocabulary as vocab
from app.services.assertions.families import (
    Chunk,
    FamilyReading,
    normalize_clause_text,
    sentence_around,
)
from app.services.assertions.families.clause_patterns import patterns_for

__all__ = [
    "FAMILY",
    "extract",
    "verdict_for",
    "confidence_for_distinct_hits",
    "EMPTY_SET_CONFIDENCE",
]

FAMILY: str = vocab.FAMILY_PRESENCE

#: How sure the parser is that a clause is present, by the number of DISTINCT
#: phrasings that matched anywhere in the retrieved chunks. Capped below 1.0
#: because no regex table is certain, and a calibrator handed a 1.0 has
#: nothing left to shrink.
_CONFIDENCE_BY_DISTINCT_HITS: tuple[float, ...] = (0.0, 0.62, 0.82, 0.91, 0.94)

#: Added once when at least one match sat on a line that reads like a heading.
_HEADING_BONUS: float = 0.04

#: What the parser reports when nothing matched at all. Deliberately low:
#: "the clause is not in the chunks retrieval returned" and "the clause is not
#: in the document" are different statements and this parser can only make the
#: first one.
EMPTY_SET_CONFIDENCE: float = 0.45


def confidence_for_distinct_hits(count: int, *, heading: bool = False) -> float:
    """Distinct-phrasing count -> parser confidence."""
    if count <= 0:
        return EMPTY_SET_CONFIDENCE
    index = min(count, len(_CONFIDENCE_BY_DISTINCT_HITS) - 1)
    value = _CONFIDENCE_BY_DISTINCT_HITS[index]
    if heading:
        value = min(0.97, value + _HEADING_BONUS)
    return value


def _looks_like_heading(body: str, start: int) -> bool:
    line_start = body.rfind("\n", 0, start) + 1
    line_end = body.find("\n", start)
    line = body[line_start : line_end if line_end != -1 else len(body)].strip()
    if not line or len(line) > 70 or line.endswith("."):
        return False
    letters = [ch for ch in line if ch.isalpha()]
    if not letters:
        return False
    upper_share = sum(1 for ch in letters if ch.isupper()) / len(letters)
    numbered = bool(re.match(r"^\s*(?:\d+(?:\.\d+)*\.?|[A-Z]\.|ARTICLE|SCHEDULE|ANNEX)", line))
    return upper_share > 0.5 or numbered


def extract(
    chunk: Chunk, *, plan: Any, workspace_currency: Optional[str] = None
) -> tuple[FamilyReading, ...]:
    """One reading per DISTINCT phrasing that matched in this chunk.

    One reading per pattern, not per match. A document that repeats "personal
    data" twelve times would otherwise produce twelve readings, the agreement
    feature would read them as twelve independent corroborations, and a single
    passing mention would score like a fully drafted annex.
    """
    body = normalize_clause_text(chunk.text)
    if not body:
        return ()

    clause_key = getattr(plan, "subject", "") or ""
    patterns = patterns_for(clause_key)

    hits: list[tuple[int, int, bool]] = []
    for pattern in patterns:
        match = pattern.search(body)
        if match is None:
            continue
        hits.append((match.start(), match.end(), _looks_like_heading(body, match.start())))

    if not hits:
        return ()

    heading = any(is_heading for _, _, is_heading in hits)
    confidence = confidence_for_distinct_hits(len(hits), heading=heading)

    readings: list[FamilyReading] = []
    for start, end, is_heading in sorted(hits):
        quote, span = sentence_around(body, start, end)
        notes: list[str] = []
        if is_heading:
            notes.append("matched on what reads as a section heading")
        readings.append(
            FamilyReading(
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                quote=quote,
                span=span,
                confidence=confidence,
                literal=clause_key,
                page_number=chunk.page_number,
                notes=tuple(notes),
            )
        )
    return tuple(readings)


def verdict_for(readings: tuple[FamilyReading, ...], plan: Any) -> str:
    """Present -> PASS. Nothing found -> FAIL, at low confidence.

    ANY reading is enough, unlike the numeric families' ALL rule. The two are
    consistent: both say "the verdict is decided by the evidence that would
    make the administrator act". For a bound that is the worst number; for an
    existence check it is the first hit.
    """
    return vocab.VERDICT_PASS if readings else vocab.VERDICT_FAIL