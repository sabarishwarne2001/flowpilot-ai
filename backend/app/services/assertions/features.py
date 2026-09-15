"""ARCH-33 §4.3 step 4 — raw features, and the score calibration maps.

THE FOUR FEATURES §4.3 NAMES, AND THE TWO IT IMPLIES
====================================================

    retrieval score          how well the paragraph matched the query
    parser agreement         did every reading agree with the verdict
    extraction confidence    how sure the parser was of what it read
    contradiction count      how many readings disagreed

plus two that fall out of the families' own semantics and cannot be omitted
without making the score dishonest:

    chunk count              one corroborating paragraph is not three
    empty reading set        `absence` PASSES on nothing found, and that is
                             the one verdict in this phase that rests on the
                             ABSENCE of evidence rather than on evidence

WHY A RAW SCORE AT ALL, RATHER THAN CALIBRATING THE FEATURES DIRECTLY
=====================================================================

A calibrator fitted on six features needs enough labelled examples to estimate
six relationships. A calibrator fitted on one scalar needs enough to estimate
one, and §4.6 already admits the realistic label budget is fifty. So the
features are combined here by a FIXED, readable, auditable rule into a single
raw score, and the learned part of the system has exactly one degree of
freedom.

The trade is deliberate: the weights below are a judgement, not a fit, and
they are wrong in the third decimal place for every tenant. That does not
matter, because the calibrator's job is precisely to learn the mapping from
this score to an actual probability for this tenant and family. A monotone
calibrator is insensitive to the units of its input — only to the ORDER — so
the weights need to rank evaluations correctly, not to be correct.

THE ONE HARD RULE
=================

`quote_required and not quote_grounded` produces exactly 0.0, not a reduced
score. §4.3: "An answer whose quote is not found is treated as confidence 0."
A multiplicative penalty would leave a strong-retrieval hallucination scoring
higher than a weak-retrieval honest answer, which is the exact inversion this
guard exists to prevent.

PURE
====

Standard library only, `Decimal` for the score so it lands in
`assertion_evaluations.raw_score numeric(6,5)` without a float round trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from app.services.assertions import vocabulary as vocab

__all__ = [
    "FeatureVector",
    "raw_score",
    "features_from",
    "extraction_confidence_for",
    "SCORE_PLACES",
    "WEIGHTS",
]

#: `numeric(6,5)`: five decimal places, and a value in [0, 1] fits.
SCORE_PLACES: Decimal = Decimal("0.00001")

#: Sum to 1.0 exactly. `verify_arch33.py` asserts it, because a weight set
#: that sums to 0.97 produces a score that can never reach 1.0 and a
#: calibrator that silently learns around the deficit.
WEIGHTS: dict[str, float] = {
    "extraction_confidence": 0.45,
    "parser_agreement": 0.20,
    "retrieval_score": 0.20,
    "corroboration": 0.15,
}

#: Subtracted per contradicting reading, and capped. A contract that says
#: Net 30 in one place and Net 60 in another is not slightly less certain than
#: one that says Net 30 twice; it is a different kind of document.
_CONTRADICTION_PENALTY: float = 0.18
_MAX_CONTRADICTION_PENALTY: float = 0.54

#: Applied when a verdict rests on an empty reading set. Only `absence` can
#: reach PASS this way, and the penalty is what guarantees the resulting score
#: sits below any threshold `ck_ad_threshold_bounded` allows.
_EMPTY_SET_PENALTY: float = 0.35

#: Corroboration saturates: the third independent paragraph saying the same
#: thing adds almost nothing the second did not.
_CORROBORATION_BY_CHUNKS: tuple[float, ...] = (0.0, 0.70, 0.90, 1.0)


@dataclass(frozen=True)
class FeatureVector:
    """The raw features, exactly as they land in the evidence payload."""

    retrieval_score: float
    parser_agreement: float
    extraction_confidence: float
    contradiction_count: int
    chunk_count: int
    empty_reading_set: bool = False
    #: True for the LLM family. False for every deterministic family, where
    #: the parser IS the evidence and there is no quote to ground.
    quote_required: bool = False
    quote_grounded: bool = False

    def as_evidence(self) -> dict[str, Any]:
        return {
            "retrieval_score": round(float(self.retrieval_score), 5),
            "parser_agreement": round(float(self.parser_agreement), 5),
            "extraction_confidence": round(float(self.extraction_confidence), 5),
            "contradiction_count": int(self.contradiction_count),
            "chunk_count": int(self.chunk_count),
            "empty_reading_set": bool(self.empty_reading_set),
            "quote_required": bool(self.quote_required),
            "quote_grounded": bool(self.quote_grounded),
        }


def _clamp(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def _corroboration(chunk_count: int) -> float:
    if chunk_count <= 0:
        return 0.0
    index = min(chunk_count, len(_CORROBORATION_BY_CHUNKS) - 1)
    return _CORROBORATION_BY_CHUNKS[index]


def raw_score(features: FeatureVector) -> Decimal:
    """Combine the features into a single score in [0, 1], 5 decimal places."""
    # The hard rule. Before anything else, so that no weighting can rescue it.
    if features.quote_required and not features.quote_grounded:
        return Decimal("0").quantize(SCORE_PLACES)

    value = (
        WEIGHTS["extraction_confidence"] * _clamp(features.extraction_confidence)
        + WEIGHTS["parser_agreement"] * _clamp(features.parser_agreement)
        + WEIGHTS["retrieval_score"] * _clamp(features.retrieval_score)
        + WEIGHTS["corroboration"] * _corroboration(features.chunk_count)
    )

    penalty = min(
        _MAX_CONTRADICTION_PENALTY,
        _CONTRADICTION_PENALTY * max(0, int(features.contradiction_count)),
    )
    value -= penalty

    if features.empty_reading_set:
        value -= _EMPTY_SET_PENALTY

    return Decimal(str(_clamp(value))).quantize(SCORE_PLACES, rounding=ROUND_HALF_UP)


def extraction_confidence_for(reading_set: Any, family: str) -> float:
    """The confidence to carry forward, including for an empty reading set.

    An empty set is not zero confidence. `absence` reporting "I found no
    auto-renewal language" is a real, weak observation with a real, low
    confidence attached — the family's own `EMPTY_SET_CONFIDENCE` — and
    flattening it to 0 would make every `absence` PASS score identically
    regardless of how much text was actually searched.

    For the numeric families an empty set means UNDETERMINED, which never
    reaches the `pass` edge anyway, and 0.0 is the honest number.
    """
    readings = getattr(reading_set, "readings", ()) or ()
    if readings:
        best = getattr(reading_set, "best", None)
        if best is not None:
            return float(best.confidence)
        return float(max(reading.confidence for reading in readings))

    from app.services.assertions.families import parser_for  # noqa: PLC0415

    module = parser_for(family)
    return float(getattr(module, "EMPTY_SET_CONFIDENCE", 0.0))


def features_from(
    reading_set: Any,
    *,
    family: str,
    retrieval_score: float,
    chunk_count: int,
    quote_required: bool = False,
    quote_grounded: bool = False,
    extraction_confidence: Optional[float] = None,
) -> FeatureVector:
    """Build the vector from a `families.ReadingSet`.

    `extraction_confidence` is an override for the LLM family, which has no
    reading set at all: its confidence is the quote verdict's.
    """
    readings = getattr(reading_set, "readings", ()) or ()
    resolved = (
        float(extraction_confidence)
        if extraction_confidence is not None
        else extraction_confidence_for(reading_set, family)
    )
    return FeatureVector(
        retrieval_score=_clamp(float(retrieval_score)),
        parser_agreement=float(getattr(reading_set, "agreement", 0.0) or 0.0),
        extraction_confidence=_clamp(resolved),
        contradiction_count=int(getattr(reading_set, "contradictions", 0) or 0),
        chunk_count=int(chunk_count),
        empty_reading_set=not readings and family != vocab.FAMILY_LLM,
        quote_required=bool(quote_required),
        quote_grounded=bool(quote_grounded),
    )