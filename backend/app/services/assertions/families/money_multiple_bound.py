"""ARCH-33 — `money_multiple_bound`: a cap expressed as a multiple of a basis.

    Liability is capped at no more than 1x annual contract value
      -> liability_cap <= 1.0 x annual_value

WHAT MAKES THIS FAMILY DIFFERENT FROM `money_bound`
===================================================

`money_bound` compares an amount to an amount. This family compares a FACTOR
to a factor, and the amount never has to be resolved at all.

That is the whole reason it exists as a separate family. "The aggregate
liability shall not exceed the total fees paid in the twelve months preceding
the claim" states a multiple of 1.0 without stating a single number, and a
money parser finds nothing in it. Trying to resolve the basis to rupees would
mean reading the contract value off the document, which is a second extraction
with its own failure mode, and getting it wrong turns a clean 1x cap into a
variance a human has to unpick.

So: read the factor, read which basis it is a multiple of, compare factors.
When the contract states a multiple of a DIFFERENT basis than the rule does
("2x monthly fees" against a rule about annual value), the parser reports the
mismatch and refuses rather than converting — twelve monthly fees are not one
annual value on a contract with a ramp, and the arithmetic that says they are
is the kind a reviewer never gets shown.

THE UNSTATED 1.0
================

"shall not exceed the fees paid" states no number. It is a multiple of one,
every commercial lawyer reads it that way, and a parser that requires a digit
misses the single most common liability cap in software contracts. It is read
as 1.0 with a lower confidence and a note saying so, which puts the decision
where §4.9 says it belongs.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Optional

from app.services.assertions import vocabulary as vocab
from app.services.assertions.families import (
    Chunk,
    FamilyReading,
    normalize_clause_text,
    sentence_around,
)

__all__ = ["FAMILY", "extract", "satisfies", "verdict_for", "BASES"]

FAMILY: str = vocab.FAMILY_MONEY_MULTIPLE_BOUND

#: `(pattern, canonical basis)`. The canonical names match the compiler's
#: `_MULTIPLE_BASES`, and `verify_arch33.py` asserts the two sets agree — a
#: basis the compiler can emit and this parser cannot recognise is a rule that
#: can be saved and can never pass.
BASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:annual|yearly|per\s+annum)\s+(?:contract\s+)?"
            r"(?:value|fees?|charges?|revenue)\b",
            re.IGNORECASE,
        ),
        "annual_value",
    ),
    (
        re.compile(
            r"\bfees?\s+paid\s+(?:by\s+[\w\s]{0,30}?)?(?:in|during|over)\s+the\s+"
            r"(?:twelve|12)\s+months?\b",
            re.IGNORECASE,
        ),
        "annual_value",
    ),
    (
        re.compile(r"\b(?:total|aggregate)\s+contract\s+value\b", re.IGNORECASE),
        "contract_value",
    ),
    (
        re.compile(
            r"\b(?:fees?|amounts?|charges?|sums?)\s+(?:actually\s+)?paid\b",
            re.IGNORECASE,
        ),
        "fees_paid",
    ),
    (
        re.compile(r"\bmonthly\s+fees?\b", re.IGNORECASE),
        "monthly_fees",
    ),
)

#: The cap sentence itself. Without this anchor the parser would read the "2x"
#: in a pricing schedule as a liability cap.
_CAP_ANCHOR = re.compile(
    r"\b(?:liability|liable|indemnit|cap(?:ped)?|shall\s+not\s+exceed|"
    r"in\s+no\s+event|limitation\s+of\s+liability|aggregate)\b",
    re.IGNORECASE,
)

_FACTOR = re.compile(
    r"\b(?P<factor>\d+(?:\.\d+)?|one|two|three|four|five|ten)\s*"
    r"(?:x|\u00d7|times|multiplied\s+by)\b",
    re.IGNORECASE,
)

_WORD_NUMBERS: dict[str, Decimal] = {
    "one": Decimal("1"),
    "two": Decimal("2"),
    "three": Decimal("3"),
    "four": Decimal("4"),
    "five": Decimal("5"),
    "ten": Decimal("10"),
}

#: Confidence for a cap that states its factor explicitly.
_EXPLICIT_CONFIDENCE: float = 0.93
#: Confidence for "shall not exceed the fees paid", read as an implied 1.0.
_IMPLIED_CONFIDENCE: float = 0.66
#: Confidence when the contract's basis is not the rule's basis. Low enough
#: that routing sends it to a human; non-zero because the reading is real.
_BASIS_MISMATCH_CONFIDENCE: float = 0.30


def _basis_in(text: str) -> Optional[str]:
    for pattern, name in BASES:
        if pattern.search(text):
            return name
    return None


def extract(
    chunk: Chunk, *, plan: Any, workspace_currency: Optional[str] = None
) -> tuple[FamilyReading, ...]:
    body = normalize_clause_text(chunk.text)
    if not body:
        return ()

    wanted_basis = getattr(plan, "basis", None)
    found: list[FamilyReading] = []
    seen_spans: set[tuple[int, int]] = set()

    for match in _FACTOR.finditer(body):
        quote, span = sentence_around(body, match.start(), match.end())
        if not _CAP_ANCHOR.search(quote):
            continue
        basis = _basis_in(quote)
        if basis is None:
            continue

        token = match.group("factor").lower()
        factor = _WORD_NUMBERS.get(token)
        if factor is None:
            factor = Decimal(token)

        notes: list[str] = []
        confidence = _EXPLICIT_CONFIDENCE
        if wanted_basis and basis != wanted_basis:
            confidence = _BASIS_MISMATCH_CONFIDENCE
            notes.append(
                f"the contract caps liability against {basis}, the rule is "
                f"written against {wanted_basis}; these are not "
                "interchangeable and a reviewer has to decide"
            )

        seen_spans.add(span)
        found.append(
            FamilyReading(
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                quote=quote,
                span=span,
                confidence=confidence,
                value=factor,
                unit=vocab.UNIT_MULTIPLE,
                literal=basis,
                page_number=chunk.page_number,
                notes=tuple(notes),
            )
        )

    # The implied 1.0: a cap sentence that names a basis and states no factor.
    for pattern, basis in BASES:
        for match in pattern.finditer(body):
            quote, span = sentence_around(body, match.start(), match.end())
            if span in seen_spans:
                continue
            if not _CAP_ANCHOR.search(quote):
                continue
            if _FACTOR.search(quote):
                continue

            notes = [
                "the clause states no multiplier, which reads as one times "
                "this basis"
            ]
            confidence = _IMPLIED_CONFIDENCE
            if wanted_basis and basis != wanted_basis:
                confidence = _BASIS_MISMATCH_CONFIDENCE
                notes.append(
                    f"the contract caps liability against {basis}, the rule is "
                    f"written against {wanted_basis}"
                )

            seen_spans.add(span)
            found.append(
                FamilyReading(
                    chunk_id=chunk.chunk_id,
                    chunk_index=chunk.chunk_index,
                    quote=quote,
                    span=span,
                    confidence=confidence,
                    value=Decimal("1.0"),
                    unit=vocab.UNIT_MULTIPLE,
                    literal=basis,
                    page_number=chunk.page_number,
                    notes=tuple(notes),
                )
            )
            break

    return tuple(found)


def satisfies(*, factor: Decimal, operator: str, bound: Decimal) -> bool:
    """Compare two dimensionless factors against the same basis."""
    if operator == vocab.OP_LE:
        return factor <= bound
    if operator == vocab.OP_LT:
        return factor < bound
    if operator == vocab.OP_GE:
        return factor >= bound
    if operator == vocab.OP_GT:
        return factor > bound
    if operator == vocab.OP_EQ:
        return factor == bound
    raise ValueError(
        f"{operator!r} is not a comparison a multiple bound can make."
    )


def verdict_for(readings: tuple[FamilyReading, ...], plan: Any) -> str:
    """PASS only when every cap found satisfies the bound on the SAME basis.

    A reading against a different basis is UNDETERMINED, never FAIL. The
    contract may well be compliant; nobody here can say, because the two
    numbers are denominated in different things. Returning FAIL would put a
    compliant supplier in front of a reviewer as a violation, which is a worse
    error than putting an unknown in front of them as an unknown.
    """
    if not readings:
        return vocab.VERDICT_UNDETERMINED
    if plan.bound is None:
        return vocab.VERDICT_UNDETERMINED

    bound = Decimal(str(plan.bound))
    wanted_basis = getattr(plan, "basis", None)

    for reading in readings:
        if reading.value is None:
            return vocab.VERDICT_UNDETERMINED
        if wanted_basis and reading.literal != wanted_basis:
            return vocab.VERDICT_UNDETERMINED
        if not satisfies(
            factor=reading.value, operator=plan.operator, bound=bound
        ):
            return vocab.VERDICT_FAIL
    return vocab.VERDICT_PASS