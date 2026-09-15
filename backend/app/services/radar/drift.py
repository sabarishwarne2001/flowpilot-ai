"""ARCH-34 §5.3 — contract drift: two versions of the paper disagreeing.

WHAT THIS MODULE DOES NOT DO
============================

It does not parse clauses. Not one regular expression in this file reads a
payment term, a notice period or a liability cap.

`app/services/assertions/families/` already does that, for eight families,
with curated patterns, confidence scores, verbatim quotes and character spans,
all of it gated by `verify_arch33.py` against real contract language. A second
parser here would be a second opinion, and a second opinion that disagrees
with the first is worse than no drift detection at all: the console would show
"Net 30 in MSA-2025, Net 60 in SOW-7" while the assertion engine on the same
two documents reported them identical, and a customer would have to decide
which of their vendor's own features to believe.

So this module does the two things ARCH-33 does not: it ALIGNS clauses between
two documents, and it COMPARES two readings of the same family.

THE ALIGNMENT IS MUTUAL-BEST, NOT GREEDY-BEST
=============================================

The obvious implementation takes each left clause and pairs it with its
closest right clause. On a contract that is the obvious implementation's worst
case: an MSA has one payment-terms clause and six clauses that mention
payment, and greedy alignment happily maps three different left clauses onto
the same right clause, producing three "findings" about one disagreement.

Mutual-best requires each side to be the other's top choice. A left clause
whose best partner has a better partner of its own goes unaligned, and an
unaligned clause produces nothing. Fewer findings, each of which is about one
thing.

Ties are broken on `(chunk_index, chunk_id)` on both sides so the alignment is
reproducible: a reviewer who reopens a drift finding must see the same two
paragraphs, and iteration order over chunks is not a promise.

UNDETERMINED IS AN ANSWER, AND IT IS NOT A FINDING
==================================================

§5.3: "Unparseable differences are not findings; they are what ARCH-33
assertions are for."

When a family reads one side and not the other, this module returns
`DRIFT_UNDETERMINED` and the sweep writes NOTHING. It is the same posture
`money_bound` takes on a unit mismatch, and the reasoning is the same: a
comparison between a value and a failure to read a value is not a comparison.
Reporting it would put "Payment terms changed from Net 30 to (could not read)"
in front of a lawyer, which is an accusation about the extractor dressed as an
accusation about the counterparty.

The `DriftReading` still carries the UNDETERMINED result so the inspection
view can say why a clause pair produced nothing.

WHY presence AND absence ARE EXCLUDED
=====================================

`DRIFT_FAMILIES` is the five VALUE-BEARING families. `presence` and `absence`
answer "is this clause here at all", and a clause appearing in one version and
not the other is not drift — it is a different document. ARCH-33's assertions
are the right tool for "the DPA annex must exist", and running it here would
report every SOW as having "dropped" every MSA clause it never contained.

PURE
====

Standard library, `vocabulary`, and `app/services/assertions/` — which is
itself pure by contract and gated as such. No Session, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional, Sequence

from app.services.assertions import vocabulary as assertion_vocab
from app.services.assertions.compiler import AssertionPlan
from app.services.assertions.families import Chunk, FamilyReading, parser_for
from app.services.radar import fingerprint as fp
from app.services.radar import vocabulary as vocab

__all__ = [
    "DRIFT_FAMILIES",
    "FAMILY_SUBJECTS",
    "FAMILY_LABELS",
    "AlignedPair",
    "DriftReading",
    "DriftSettings",
    "DEFAULT_SETTINGS",
    "align",
    "probe_plan",
    "read_side",
    "compare",
    "evaluate",
]


#: The value-bearing families, in the order a drift finding reports them. Not
#: `assertion_vocab.DETERMINISTIC_FAMILIES`: `presence` and `absence` are
#: excluded for the reason in the module docstring, and `llm` has no parser at
#: all — its whole point is that it needs a quote from a model, which is not
#: something a nightly sweep across a workspace's contracts should be doing.
DRIFT_FAMILIES: tuple[str, ...] = (
    assertion_vocab.FAMILY_DURATION_BOUND,
    assertion_vocab.FAMILY_NOTICE_PERIOD,
    assertion_vocab.FAMILY_MONEY_MULTIPLE_BOUND,
    assertion_vocab.FAMILY_MONEY_BOUND,
    assertion_vocab.FAMILY_ENUMERATED,
)

#: The canonical subject each family probes for. `AssertionPlan.subject` is
#: documented as a canonical subject key, and while today's parsers do not
#: branch on it, constructing a plan with a made-up subject would be relying
#: on that staying true.
FAMILY_SUBJECTS: dict[str, str] = {
    assertion_vocab.FAMILY_DURATION_BOUND: "payment_terms",
    assertion_vocab.FAMILY_NOTICE_PERIOD: "notice_period",
    assertion_vocab.FAMILY_MONEY_MULTIPLE_BOUND: "liability_cap",
    assertion_vocab.FAMILY_MONEY_BOUND: "late_fee",
    assertion_vocab.FAMILY_ENUMERATED: "governing_law",
}

#: Plain words for the console. §5.6's headline is written from these by a
#: template — "Payment terms differ between MSA-2025 (Net 30) and SOW-7
#: (Net 60)" — never generated, so the sentence on screen is exactly what the
#: parsers read.
FAMILY_LABELS: dict[str, str] = {
    assertion_vocab.FAMILY_DURATION_BOUND: "Payment terms",
    assertion_vocab.FAMILY_NOTICE_PERIOD: "Notice period",
    assertion_vocab.FAMILY_MONEY_MULTIPLE_BOUND: "Liability cap",
    assertion_vocab.FAMILY_MONEY_BOUND: "Late payment charge",
    assertion_vocab.FAMILY_ENUMERATED: "Governing law",
}


@dataclass(frozen=True)
class DriftSettings:
    """Alignment threshold, frozen, part of the `input_digest`."""

    alignment_min: Decimal = vocab.DRIFT_ALIGNMENT_MIN
    families: tuple[str, ...] = DRIFT_FAMILIES

    def as_digest_payload(self) -> dict[str, Any]:
        return {
            "alignment_min": self.alignment_min,
            "families": list(self.families),
        }


DEFAULT_SETTINGS: DriftSettings = DriftSettings()


@dataclass(frozen=True)
class AlignedPair:
    """Two clauses, one from each document, that are about the same thing."""

    left: fp.ChunkVector
    right: fp.ChunkVector
    similarity: Decimal


@dataclass(frozen=True)
class DriftReading:
    """One family's verdict on one aligned pair."""

    family: str
    status: str
    left: Optional[FamilyReading]
    right: Optional[FamilyReading]
    similarity: Decimal
    note: str = ""
    evidence: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.status not in vocab.DRIFT_STATUSES:
            raise ValueError(
                f"{self.status!r} is not a drift status. Known: "
                f"{', '.join(vocab.DRIFT_STATUSES)}."
            )


# ===========================================================================
# Alignment
# ===========================================================================


def align(
    left: Sequence[fp.ChunkVector],
    right: Sequence[fp.ChunkVector],
    *,
    settings: DriftSettings = DEFAULT_SETTINGS,
) -> tuple[AlignedPair, ...]:
    """Mutual-best clause alignment by cosine, above the threshold.

    O(n·m) cosines. Contracts chunk into tens of passages, not thousands, and
    this runs on a nightly job over documents of role CONTRACT with the same
    vendor key — a set that is small by construction.
    """
    if not left or not right:
        return ()

    scores: dict[tuple[int, int], Decimal] = {}
    for i, left_chunk in enumerate(left):
        for j, right_chunk in enumerate(right):
            scores[(i, j)] = fp.cosine(left_chunk.vector, right_chunk.vector)

    def best_for_left(i: int) -> Optional[int]:
        candidates = [
            (j, scores[(i, j)]) for j in range(len(right))
            if scores[(i, j)] >= settings.alignment_min
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (item[1], -right[item[0]].chunk_index,
                              _reverse_key(right[item[0]].chunk_id)),
        )[0]

    def best_for_right(j: int) -> Optional[int]:
        candidates = [
            (i, scores[(i, j)]) for i in range(len(left))
            if scores[(i, j)] >= settings.alignment_min
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (item[1], -left[item[0]].chunk_index,
                              _reverse_key(left[item[0]].chunk_id)),
        )[0]

    pairs: list[AlignedPair] = []
    for i in range(len(left)):
        j = best_for_left(i)
        if j is None:
            continue
        # Mutual: the right clause must choose this left clause back.
        if best_for_right(j) != i:
            continue
        pairs.append(
            AlignedPair(left=left[i], right=right[j], similarity=scores[(i, j)])
        )

    pairs.sort(
        key=lambda pair: (
            -pair.similarity,
            pair.left.chunk_index,
            pair.left.chunk_id,
        )
    )
    return tuple(pairs)


class _ReverseString:
    """Sorts a string in reverse for use inside a `max()` key tuple.

    Needed because ties must break on the LOWEST chunk id while every other
    component of the key breaks on the highest. Negating a string is not
    possible; wrapping it is.
    """

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    def __lt__(self, other: "_ReverseString") -> bool:
        return self.value > other.value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _ReverseString) and self.value == other.value


def _reverse_key(value: str) -> _ReverseString:
    return _ReverseString(value)


# ===========================================================================
# Reading and comparison
# ===========================================================================


def probe_plan(family: str) -> AssertionPlan:
    """A plan whose only job is to make a family parser run.

    `families.read()` is not used here, and the difference matters:
    `read()` calls `verdict_for()`, which needs a BOUND — "is this value at
    most 30 days". Drift has no bound. It is not asking whether a clause
    satisfies a rule; it is asking whether two clauses say the same thing, and
    the two answers can differ. A contract that moved from Net 15 to Net 20
    has drifted, and satisfies "at most Net 30" in both versions.

    So this module calls `extract()` directly, and constructs the minimum plan
    that call needs.
    """
    if family not in DRIFT_FAMILIES:
        raise ValueError(
            f"{family!r} is not a drift family. Known: "
            f"{', '.join(DRIFT_FAMILIES)}. presence and absence answer "
            "'is this clause here', which is an ARCH-33 assertion rather "
            "than drift."
        )
    return AssertionPlan(
        family=family,
        subject=FAMILY_SUBJECTS[family],
        operator=assertion_vocab.OP_EXISTS,
        sentence=f"drift probe for {FAMILY_LABELS[family]}",
    )


def _as_assertion_chunk(chunk: fp.ChunkVector) -> Chunk:
    """The boundary between radar carriers and ARCH-33 carriers."""
    return Chunk(
        chunk_id=chunk.chunk_id,
        chunk_index=chunk.chunk_index,
        text=chunk.text,
        page_number=chunk.page_number,
        retrieval_score=0.0,
    )


def read_side(
    chunk: fp.ChunkVector,
    family: str,
    *,
    workspace_currency: Optional[str] = None,
) -> Optional[FamilyReading]:
    """The single best reading this family gets out of one clause.

    Highest confidence wins, ties broken on span so the choice is stable. A
    clause containing two day counts — "payable within thirty (30) days,
    or fifteen (15) days for expedited orders" — returns the more confident
    one, and the quote on the finding shows which sentence it came from, so a
    reviewer can see immediately that the clause is more complicated than the
    finding's headline.
    """
    readings = parser_for(family).extract(
        _as_assertion_chunk(chunk),
        plan=probe_plan(family),
        workspace_currency=workspace_currency,
    )
    if not readings:
        return None
    ordered = sorted(
        readings,
        key=lambda r: (-round(float(r.confidence), 6), r.span[0], r.span[1]),
    )
    return ordered[0]


def _values_differ(left: FamilyReading, right: FamilyReading) -> Optional[bool]:
    """Whether two readings disagree, or None when they cannot be compared.

    None on a unit mismatch. "30 days" against "1 month" are probably the same
    term and possibly not, and this module has no business deciding which:
    `app/core/normalize.py` is the only thing in the tree allowed to convert a
    duration, and it was not asked. Reporting a drift between them would be
    asserting a conversion nobody made.
    """
    if left.value is not None and right.value is not None:
        if left.unit != right.unit:
            return None
        return left.value != right.value

    if left.literal is not None and right.literal is not None:
        return left.literal.strip().casefold() != right.literal.strip().casefold()

    # One side produced a number and the other a literal. Different shapes of
    # answer, not different answers.
    return None


def compare(
    pair: AlignedPair,
    family: str,
    *,
    workspace_currency: Optional[str] = None,
) -> DriftReading:
    """Read both sides of one aligned pair with one family and compare."""
    left = read_side(pair.left, family, workspace_currency=workspace_currency)
    right = read_side(pair.right, family, workspace_currency=workspace_currency)

    if left is None or right is None:
        which = (
            "neither side"
            if left is None and right is None
            else ("the later version" if left is not None else "the earlier version")
        )
        return DriftReading(
            family=family,
            status=vocab.DRIFT_UNDETERMINED,
            left=left,
            right=right,
            similarity=pair.similarity,
            note=(
                f"{FAMILY_LABELS[family]} could not be read from {which}. "
                "Not reported: a comparison against a failure to read is not "
                "a comparison. An ARCH-33 assertion is the right tool for "
                "requiring the clause to be present and readable."
            ),
        )

    differs = _values_differ(left, right)
    if differs is None:
        return DriftReading(
            family=family,
            status=vocab.DRIFT_UNDETERMINED,
            left=left,
            right=right,
            similarity=pair.similarity,
            note=(
                f"{FAMILY_LABELS[family]} was read in different units "
                f"({left.unit or 'literal'} against {right.unit or 'literal'}). "
                "Converting between them is app/core/normalize.py's decision "
                "to make, not this engine's."
            ),
        )

    if not differs:
        return DriftReading(
            family=family,
            status=vocab.DRIFT_SAME,
            left=left,
            right=right,
            similarity=pair.similarity,
            note=f"{FAMILY_LABELS[family]} is unchanged.",
        )

    evidence: tuple[dict[str, Any], ...] = (
        {
            "kind": vocab.EVIDENCE_CLAUSE_PAIR,
            "label": FAMILY_LABELS[family],
            "family": family,
            "alignment_cosine": str(pair.similarity),
            "subject": {
                "chunk_id": pair.left.chunk_id,
                "chunk_index": pair.left.chunk_index,
                "page_number": left.page_number,
                "quote": left.quote,
                "span": list(left.span),
                "value": None if left.value is None else str(left.value),
                "unit": left.unit,
                "literal": left.literal,
                "confidence": round(float(left.confidence), 5),
            },
            "counterpart": {
                "chunk_id": pair.right.chunk_id,
                "chunk_index": pair.right.chunk_index,
                "page_number": right.page_number,
                "quote": right.quote,
                "span": list(right.span),
                "value": None if right.value is None else str(right.value),
                "unit": right.unit,
                "literal": right.literal,
                "confidence": round(float(right.confidence), 5),
            },
            "note": (
                "Clauses aligned by chunk embedding cosine, then read by the "
                "ARCH-33 family parser. The quoted sentences are verbatim."
            ),
        },
    )

    return DriftReading(
        family=family,
        status=vocab.DRIFT_CHANGED,
        left=left,
        right=right,
        similarity=pair.similarity,
        note=f"{FAMILY_LABELS[family]} differs between the two documents.",
        evidence=evidence,
    )


def evaluate(
    left: Sequence[fp.ChunkVector],
    right: Sequence[fp.ChunkVector],
    *,
    settings: DriftSettings = DEFAULT_SETTINGS,
    workspace_currency: Optional[str] = None,
) -> tuple[DriftReading, ...]:
    """Every drift reading between two contract versions, findings first.

    Returns SAME and UNDETERMINED readings alongside CHANGED ones. The sweep
    filters to `DRIFT_CHANGED` before writing; the inspection view and the
    gate need the rest, because "the engine found nothing" and "the engine
    found six clauses that agree" are very different statements about a
    contract pair and the console should be able to tell them apart.

    One reading per (pair, family) at most, and a pair that a family cannot
    read is not retried by another family — a payment-terms clause read as a
    late-fee percentage would be a coincidence, not a finding.
    """
    pairs = align(left, right, settings=settings)
    readings: list[DriftReading] = []
    for pair in pairs:
        for family in settings.families:
            reading = compare(
                pair, family, workspace_currency=workspace_currency
            )
            if reading.left is None and reading.right is None:
                # Neither side mentions this family at all. Recording it would
                # produce five UNDETERMINED readings per aligned pair and bury
                # the one that matters.
                continue
            readings.append(reading)

    order = {
        vocab.DRIFT_CHANGED: 0,
        vocab.DRIFT_UNDETERMINED: 1,
        vocab.DRIFT_SAME: 2,
    }
    readings.sort(
        key=lambda r: (
            order[r.status],
            settings.families.index(r.family)
            if r.family in settings.families
            else len(settings.families),
            -r.similarity,
        )
    )
    return tuple(readings)
