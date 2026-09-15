"""ARCH-33 — `enumerated`: the value must be one of a permitted set.

    Governing law is India or Singapore   -> governing_law in {IN, SG}

WHY THE PLAN HOLDS CODES AND THE DOCUMENT HOLDS PROSE
=====================================================

The compiler turns "India or Singapore" into `{IN, SG}` and this parser turns
"the laws of the Republic of India" into `IN`. Neither side compares the words
the other side used, and that is deliberate: a contract says "England and
Wales", "the State of New York", "Republic of Singapore" and "Singapore"
interchangeably, and an administrator types whichever one they think of. One
canonical form in the middle is the only arrangement where the number of
spellings the product has to know is additive rather than multiplicative.

THE VALUE IS WHAT THE CLAUSE SAYS, NOT WHAT THE DOCUMENT MENTIONS
================================================================

A contract naming India in the address block, Singapore in the arbitration
clause and Delaware in the parent-company recital mentions three jurisdictions
and is governed by one. So a reading only counts when the canonical value sits
in a sentence that also carries the SUBJECT anchor — "governed by", "governing
law", "exclusive jurisdiction". Without that anchor this family would report
every place name on the page and the agreement feature would then describe a
document as wildly self-contradictory when it is simply long.

A VALUE OUTSIDE THE SET IS A FAIL, AND A VALUE THE PARSER CANNOT NAME IS NOT
===========================================================================

"Governed by the laws of the Cayman Islands" against a rule permitting
`{IN, SG}` is a FAIL: a value was read and it is not in the set. "Governed by
the laws set out in Schedule 4" is UNDETERMINED: nothing was read. Collapsing
the two would either wave through a cross-reference or raise a violation
against a contract whose governing law the parser simply did not know how to
name — §4.9 names the cross-reference case explicitly.
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

__all__ = ["FAMILY", "extract", "verdict_for", "CANONICAL_VALUES"]

FAMILY: str = vocab.FAMILY_ENUMERATED

#: `(pattern, canonical code)`. Longest / most specific first: "England and
#: Wales" must win over "England", and "New York" must not be found inside
#: "New York Convention", which is why the arbitration phrase is excluded
#: explicitly below rather than by hoping the anchor filters it.
CANONICAL_VALUES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bengland\s+and\s+wales\b", re.IGNORECASE), "GB"),
    (re.compile(r"\b(?:united\s+kingdom|u\.?k\.?)\b", re.IGNORECASE), "GB"),
    (re.compile(r"\bengland\b", re.IGNORECASE), "GB"),
    (re.compile(r"\b(?:state\s+of\s+)?delaware\b", re.IGNORECASE), "US-DE"),
    (
        re.compile(
            r"\b(?:state\s+of\s+)?new\s+york\b(?!\s+convention)", re.IGNORECASE
        ),
        "US-NY",
    ),
    (re.compile(r"\b(?:state\s+of\s+)?california\b", re.IGNORECASE), "US-CA"),
    (
        re.compile(
            r"\bunited\s+states(?:\s+of\s+america)?\b|\bu\.?s\.?a\.?\b",
            re.IGNORECASE,
        ),
        "US",
    ),
    (re.compile(r"\b(?:republic\s+of\s+)?india\b", re.IGNORECASE), "IN"),
    (re.compile(r"\b(?:republic\s+of\s+)?singapore\b", re.IGNORECASE), "SG"),
    (re.compile(r"\baustralia\b", re.IGNORECASE), "AU"),
    (re.compile(r"\bcanada\b", re.IGNORECASE), "CA"),
    (re.compile(r"\bgermany\b", re.IGNORECASE), "DE"),
    (re.compile(r"\bfrance\b", re.IGNORECASE), "FR"),
    (re.compile(r"\b(?:the\s+)?netherlands\b", re.IGNORECASE), "NL"),
    (re.compile(r"\bireland\b", re.IGNORECASE), "IE"),
    (re.compile(r"\bswitzerland\b", re.IGNORECASE), "CH"),
    (re.compile(r"\bjapan\b", re.IGNORECASE), "JP"),
    (re.compile(r"\bhong\s+kong\b", re.IGNORECASE), "HK"),
    (
        re.compile(r"\bunited\s+arab\s+emirates\b|\bu\.?a\.?e\.?\b", re.IGNORECASE),
        "AE",
    ),
    (re.compile(r"\bcayman\s+islands\b", re.IGNORECASE), "KY"),
    (re.compile(r"\b(?:inr|indian\s+rupees?|rupees?)\b", re.IGNORECASE), "INR"),
    (re.compile(r"\b(?:usd|us\s+dollars?|united\s+states\s+dollars?)\b", re.IGNORECASE), "USD"),
    (re.compile(r"\b(?:eur|euros?)\b", re.IGNORECASE), "EUR"),
    (re.compile(r"\b(?:gbp|pounds?\s+sterling)\b", re.IGNORECASE), "GBP"),
    (re.compile(r"\b(?:sgd|singapore\s+dollars?)\b", re.IGNORECASE), "SGD"),
)

#: The sentence must be ABOUT the subject, not merely contain a place name.
_SUBJECT_ANCHORS: dict[str, re.Pattern[str]] = {
    "governing_law": re.compile(
        r"\bgovern(?:ed|ing)\b|\bgoverning\s+law\b|\bconstrued\s+in\s+accordance\b"
        r"|\bapplicable\s+law\b|\bchoice\s+of\s+law\b",
        re.IGNORECASE,
    ),
    "jurisdiction": re.compile(
        r"\bjurisdiction\b|\bcourts?\s+of\b|\bvenue\b|\bsubmit\s+to\s+the\b",
        re.IGNORECASE,
    ),
    "currency": re.compile(
        r"\bcurrency\b|\bdenominated\s+in\b|\bpayable\s+in\b|\binvoiced\s+in\b",
        re.IGNORECASE,
    ),
}

#: A canonical value inside a sentence carrying the subject anchor.
_ANCHORED_CONFIDENCE: float = 0.91
#: Two or more DIFFERENT canonical values in the same anchored sentence:
#: "governed by the laws of India, save that Singapore law applies to...".
#: A real construction, and one no parser should resolve on its own.
_MULTI_VALUE_CONFIDENCE: float = 0.40


def extract(
    chunk: Chunk, *, plan: Any, workspace_currency: Optional[str] = None
) -> tuple[FamilyReading, ...]:
    body = normalize_clause_text(chunk.text)
    if not body:
        return ()

    anchor = _SUBJECT_ANCHORS.get(getattr(plan, "subject", "") or "")
    found: list[FamilyReading] = []
    claimed: list[tuple[int, int]] = []

    for pattern, code in CANONICAL_VALUES:
        for match in pattern.finditer(body):
            start, end = match.span()
            if any(start < c_end and c_start < end for c_start, c_end in claimed):
                continue
            quote, span = sentence_around(body, start, end)
            if anchor is not None and not anchor.search(quote):
                continue
            claimed.append((start, end))
            found.append(
                FamilyReading(
                    chunk_id=chunk.chunk_id,
                    chunk_index=chunk.chunk_index,
                    quote=quote,
                    span=span,
                    confidence=_ANCHORED_CONFIDENCE,
                    literal=code,
                    unit=None,
                    page_number=chunk.page_number,
                )
            )

    # Two different values in one sentence: drop every reading in that
    # sentence to the multi-value confidence rather than picking one.
    by_quote: dict[str, set[str]] = {}
    for reading in found:
        by_quote.setdefault(reading.quote, set()).add(str(reading.literal))

    adjusted: list[FamilyReading] = []
    for reading in found:
        if len(by_quote.get(reading.quote, set())) > 1:
            adjusted.append(
                FamilyReading(
                    chunk_id=reading.chunk_id,
                    chunk_index=reading.chunk_index,
                    quote=reading.quote,
                    span=reading.span,
                    confidence=_MULTI_VALUE_CONFIDENCE,
                    literal=reading.literal,
                    unit=reading.unit,
                    page_number=reading.page_number,
                    notes=(
                        "this sentence names more than one jurisdiction or "
                        "currency; which one governs is a reading decision",
                    ),
                )
            )
        else:
            adjusted.append(reading)

    return tuple(adjusted)


def verdict_for(readings: tuple[FamilyReading, ...], plan: Any) -> str:
    """PASS only when every value read is in the permitted set."""
    if not readings:
        return vocab.VERDICT_UNDETERMINED
    permitted = set(getattr(plan, "values", ()) or ())
    if not permitted:
        return vocab.VERDICT_UNDETERMINED

    for reading in readings:
        if not reading.literal:
            return vocab.VERDICT_UNDETERMINED
        if reading.literal not in permitted:
            return vocab.VERDICT_FAIL
    return vocab.VERDICT_PASS