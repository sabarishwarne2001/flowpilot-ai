"""ARCH-33 §4.5 — the deterministic family parsers.

ONE PARSER PER FAMILY, EACH RETURNING VALUE, UNIT, THE CHUNK IT CAME FROM AND
A PARSER CONFIDENCE
===========================================================================

The four things a parser returns are the four things a reviewer needs and the
four things the feature vector consumes. Dropping any one of them produces a
recognisable failure:

  value      without it there is nothing to compare
  unit       without it "30" from a days clause and "30" from a percentage
             clause are the same number
  chunk      without it the review queue has a verdict and no paragraph to
             highlight, which is the one thing §4.1 sells
  confidence without it every extraction looks equally certain and the
             calibrated probability has nothing to work from

WHY THE VERDICT LIVES IN THE FAMILY MODULE AND NOT HERE
=======================================================

`presence` with no readings is a FAIL. `absence` with no readings is a PASS.
Those are opposite answers to the same observation, and a shared "no readings
means undetermined" rule in this module would be wrong for both. So each
family owns `verdict_for()` and this module only aggregates.

That also gives the mutation suite a real target: comparator inversion is a
one-line edit inside `duration_bound.satisfies`, not an abstraction three
modules away from the semantics it changes.

EVIDENCE OF ABSENCE IS WEAKER THAN EVIDENCE OF PRESENCE
=======================================================

`absence` reaching PASS on an empty reading set is the single most dangerous
path in this phase: a retrieval failure and a genuinely clean contract look
identical from here. So the family returns PASS with a deliberately low parser
confidence, the feature vector carries that through, and the calibrated
probability lands under any sane threshold — which routes it to a human. The
safety property is not "the parser is careful"; it is "the parser reports how
little it knows and the routing rule does the rest".

PURE
====

`Chunk` is a plain dataclass, not a `DocumentChunk`. Nothing under this
package imports the ORM, opens a Session or reads settings. `retrieve.py`
converts ARCH-11 results into `Chunk` values at the boundary, which is what
lets `verify_arch33.py` drive every parser against curated clause text with no
database at all.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional, Sequence

from app.services.assertions import vocabulary as vocab

__all__ = [
    "Chunk",
    "FamilyReading",
    "ReadingSet",
    "PARSERS",
    "parser_for",
    "read",
    "sentence_around",
    "normalize_clause_text",
    "MAX_QUOTE_CHARS",
]

#: A quote longer than this is not a sentence, it is a paragraph, and pasting
#: a paragraph into the review queue as "the sentence relied upon" defeats the
#: purpose of highlighting one.
MAX_QUOTE_CHARS: int = 400


@dataclass(frozen=True)
class Chunk:
    """One retrieved passage, decoupled from `DocumentChunk`.

    `retrieval_score` is the fused hybrid score ARCH-11 returned, carried
    through unchanged. It is a FEATURE, not a filter: a low-scoring chunk that
    nevertheless contains an unambiguous "Net 30" is better evidence than a
    high-scoring chunk of definitions, and letting the score decide before the
    parser runs would discard the first.
    """

    chunk_id: str
    chunk_index: int
    text: str
    page_number: Optional[int] = None
    retrieval_score: float = 0.0
    work_item_id: Optional[str] = None
    page_start_char: Optional[int] = None
    bbox: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class FamilyReading:
    """What one parser found in one chunk."""

    chunk_id: str
    chunk_index: int
    #: The verbatim sentence the value was read out of. This is what the
    #: review queue highlights and what the manifest of evidence records.
    quote: str
    #: Character offsets of `quote` within the chunk's text.
    span: tuple[int, int]
    confidence: float
    value: Optional[Decimal] = None
    unit: Optional[str] = None
    #: `enumerated` puts the canonical code here; `presence` and `absence` put
    #: the clause phrase that matched.
    literal: Optional[str] = None
    page_number: Optional[int] = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_evidence(self) -> dict[str, Any]:
        """The shape that lands in `assertion_evaluations.evidence`."""
        return {
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "page_number": self.page_number,
            "quote": self.quote,
            "span": list(self.span),
            "value": None if self.value is None else str(self.value),
            "unit": self.unit,
            "literal": self.literal,
            "confidence": round(float(self.confidence), 5),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ReadingSet:
    """Every reading a family produced across every retrieved chunk."""

    family: str
    readings: tuple[FamilyReading, ...]
    verdict: str
    #: The reading the verdict rests on, and the one whose paragraph the
    #: review queue highlights. None when nothing was read.
    best: Optional[FamilyReading]
    #: Share of readings whose own verdict matches the aggregate verdict, in
    #: [0, 1]. One reading is trivially 1.0; that is correct and the feature
    #: vector separately knows how many readings there were.
    agreement: float
    #: Readings that disagree with the aggregate verdict. A contract that says
    #: "Net 30" in the terms and "Net 60" in an annex produces one, and a
    #: reviewer should see it rather than the engine picking the higher score.
    contradictions: int

    def as_evidence(self) -> list[dict[str, Any]]:
        return [reading.as_evidence() for reading in self.readings]


# ===========================================================================
# Text helpers shared by every parser
# ===========================================================================

_SENTENCE_END = re.compile(r"(?<=[.;!?])\s+(?=[A-Z(\u201c\"])|\n{2,}")


def normalize_clause_text(text: str) -> str:
    """Fold to a comparable form without moving any character positions.

    Length-preserving on purpose: parsers report spans into the ORIGINAL chunk
    text so the review queue can highlight the right characters, and a
    normaliser that collapsed whitespace would shift every offset after the
    first double space. Ligatures and accents are folded through NFKC rather
    than NFKD for the same reason — NFKD expands, NFKC does not.
    """
    folded = unicodedata.normalize("NFKC", text or "")
    folded = (
        folded.replace("\u00a0", " ")
        .replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )
    if len(folded) != len(text or ""):
        # NFKC changed the length (a ligature, a full-width form). Fall back to
        # the original rather than return offsets that point at the wrong
        # characters. Losing a fold is a recall cost; losing offset alignment
        # highlights the wrong paragraph, which is a correctness cost.
        return text or ""
    return folded


def sentence_around(text: str, start: int, end: int) -> tuple[str, tuple[int, int]]:
    """The sentence containing `[start, end)`, and its span in `text`.

    Contracts are written in long sentences with semicolons and numbered
    sub-clauses, so the boundary set is `. ; ! ?` followed by whitespace and a
    capital, plus blank lines. Splitting on a bare period would cut
    "Rs. 5,000" and "Net 30. days" in half and hand the reviewer a fragment.
    """
    body = text or ""
    if not body:
        return "", (0, 0)

    start = max(0, min(start, len(body)))
    end = max(start, min(end, len(body)))

    boundaries = [0] + [m.end() for m in _SENTENCE_END.finditer(body)] + [len(body)]
    left = 0
    right = len(body)
    for index, boundary in enumerate(boundaries):
        if boundary <= start:
            left = boundary
        if boundary >= end:
            right = boundary
            break
        if index == len(boundaries) - 1:
            right = len(body)

    quote = body[left:right].strip()
    offset = left + (len(body[left:right]) - len(body[left:right].lstrip()))

    if len(quote) > MAX_QUOTE_CHARS:
        # Too long to be a quote. Window it around the match instead, and keep
        # the span honest so the highlight still lands on the right characters.
        half = MAX_QUOTE_CHARS // 2
        window_start = max(left, start - half)
        window_end = min(right, window_start + MAX_QUOTE_CHARS)
        quote = body[window_start:window_end].strip()
        offset = window_start
        return quote, (offset, offset + len(quote))

    return quote, (offset, offset + len(quote))


# ===========================================================================
# Dispatch
# ===========================================================================


def parser_for(family: str) -> Any:
    """The module that parses `family`, refusing an unknown one loudly."""
    from app.services.assertions.families import (  # noqa: PLC0415
        absence,
        duration_bound,
        enumerated,
        money_bound,
        money_multiple_bound,
        notice_period,
        presence,
    )

    registry = {
        vocab.FAMILY_DURATION_BOUND: duration_bound,
        vocab.FAMILY_NOTICE_PERIOD: notice_period,
        vocab.FAMILY_MONEY_MULTIPLE_BOUND: money_multiple_bound,
        vocab.FAMILY_MONEY_BOUND: money_bound,
        vocab.FAMILY_ENUMERATED: enumerated,
        vocab.FAMILY_PRESENCE: presence,
        vocab.FAMILY_ABSENCE: absence,
    }
    if family not in registry:
        raise ValueError(
            f"{family!r} has no deterministic parser. The llm family is "
            "evaluated by `evaluate.py` with a mandatory quote, not here."
        )
    return registry[family]


#: Every family this package parses. Asserted equal to
#: `vocabulary.DETERMINISTIC_FAMILIES` by `verify_arch33.py`, so a family added
#: to the vocabulary without a parser fails a gate rather than raising a
#: KeyError at run time inside a worker.
PARSERS: tuple[str, ...] = vocab.DETERMINISTIC_FAMILIES


def read(
    plan: Any,
    chunks: Sequence[Chunk],
    *,
    workspace_currency: Optional[str] = None,
) -> ReadingSet:
    """Run one family's parser over every chunk and aggregate the result."""
    module = parser_for(plan.family)

    readings: list[FamilyReading] = []
    for chunk in chunks:
        readings.extend(
            module.extract(chunk, plan=plan, workspace_currency=workspace_currency)
        )

    # Highest-confidence first, then the chunk that retrieval ranked higher,
    # then chunk index. Fully deterministic: two runs over the same inputs
    # must select the same paragraph, or the evidence a reviewer approved is
    # not the evidence the next run shows.
    order = {chunk.chunk_id: index for index, chunk in enumerate(chunks)}
    readings.sort(
        key=lambda r: (
            -round(float(r.confidence), 6),
            order.get(r.chunk_id, 1_000_000),
            r.chunk_index,
            r.span[0],
        )
    )

    verdict = module.verdict_for(tuple(readings), plan)

    per_reading = [module.verdict_for((reading,), plan) for reading in readings]
    matching = sum(1 for value in per_reading if value == verdict)
    agreement = (matching / len(per_reading)) if per_reading else 0.0
    contradictions = len(per_reading) - matching

    best: Optional[FamilyReading] = None
    for reading, own in zip(readings, per_reading):
        if own == verdict:
            best = reading
            break
    if best is None and readings:
        best = readings[0]

    return ReadingSet(
        family=plan.family,
        readings=tuple(readings),
        verdict=verdict,
        best=best,
        agreement=round(agreement, 6),
        contradictions=contradictions,
    )