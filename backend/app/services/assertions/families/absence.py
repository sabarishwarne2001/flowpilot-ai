"""ARCH-33 — `absence`: the document must NOT contain a named clause.

    There is no automatic renewal
      -> not exists(clause: auto renewal)

THE ASYMMETRY THAT GOVERNS THIS MODULE
======================================

Every other family fails safe by finding nothing: `presence` with no readings
FAILS, a numeric family with no readings is UNDETERMINED, and both send the
document to a human. This family is the exception. Finding nothing is its PASS
condition, so a retrieval miss, an OCR failure, a chunker that split the
renewal clause across a boundary, or a contract that phrases auto-renewal in
words this table does not know ALL produce the same output as a genuinely
clean contract: PASS.

That is the single most dangerous path in ARCH-33, and the mitigation is not
"write better patterns". It is:

  1. PASS on an empty reading set is reported at `EMPTY_SET_CONFIDENCE`, which
     is deliberately below any threshold `ck_ad_threshold_bounded` permits
     (> 0.5), so it can never be the sole basis of an automatic pass.
  2. `features.py` carries the empty-set flag through to the raw score.
  3. `routing.py` compares the CALIBRATED probability, and a calibrator fitted
     on reviewer labels for this family learns exactly how often an empty
     reading set was wrong for this tenant.

So the safety property is not that the parser is thorough. It is that the
parser reports how little it knows and the routing rule refuses to act on it
until a tenant's own reviewers have shown it can be trusted.

FINDING THE CLAUSE IS THE HIGH-CONFIDENCE ANSWER
================================================

The inverse holds and is worth stating: a match here is strong evidence. A
contract containing "shall automatically renew for successive twelve month
periods" does have an auto-renewal clause, and the FAIL this produces is one a
reviewer will almost always confirm. Confidence is therefore high on FAIL and
low on PASS — the opposite shape to `presence`, and the reason these are two
families rather than one with a negation flag.
"""

from __future__ import annotations

from typing import Any, Optional

from app.services.assertions import vocabulary as vocab
from app.services.assertions.families import (
    Chunk,
    FamilyReading,
    normalize_clause_text,
    sentence_around,
)
from app.services.assertions.families.clause_patterns import patterns_for
from app.services.assertions.families.presence import (
    EMPTY_SET_CONFIDENCE,
    confidence_for_distinct_hits,
)

__all__ = ["FAMILY", "extract", "verdict_for", "EMPTY_SET_CONFIDENCE"]

FAMILY: str = vocab.FAMILY_ABSENCE


def extract(
    chunk: Chunk, *, plan: Any, workspace_currency: Optional[str] = None
) -> tuple[FamilyReading, ...]:
    """Every distinct phrasing of the excluded clause found in this chunk.

    Detection is `presence`'s, verbatim, through the shared pattern table. The
    two families must never disagree about whether a clause is in a document;
    they disagree only about what that means.
    """
    body = normalize_clause_text(chunk.text)
    if not body:
        return ()

    clause_key = getattr(plan, "subject", "") or ""
    patterns = patterns_for(clause_key)

    hits: list[tuple[int, int]] = []
    for pattern in patterns:
        match = pattern.search(body)
        if match is not None:
            hits.append((match.start(), match.end()))

    if not hits:
        return ()

    confidence = confidence_for_distinct_hits(len(hits))

    readings: list[FamilyReading] = []
    for start, end in sorted(hits):
        quote, span = sentence_around(body, start, end)
        readings.append(
            FamilyReading(
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                quote=quote,
                span=span,
                confidence=confidence,
                literal=clause_key,
                page_number=chunk.page_number,
                notes=(
                    "this is the clause the rule excludes; the quote is the "
                    "reason the document does not pass",
                ),
            )
        )
    return tuple(readings)


def verdict_for(readings: tuple[FamilyReading, ...], plan: Any) -> str:
    """Found -> FAIL. Nothing found -> PASS, and see the module header."""
    return vocab.VERDICT_FAIL if readings else vocab.VERDICT_PASS