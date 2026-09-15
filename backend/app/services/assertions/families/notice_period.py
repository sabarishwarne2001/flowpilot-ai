"""ARCH-33 — `notice_period`: how much warning a party must give.

WHY THIS IS NOT `duration_bound` WITH A DIFFERENT SUBJECT
========================================================

Both families count days and both share `duration_bound.satisfies`, which is
imported rather than copied precisely so an inversion there cannot be fixed in
one family and left in the other.

What differs is what counts as a candidate. A payment clause announces itself
with "Net", "payable within" and "due"; a notice clause announces itself with
"written notice", "terminate" and "prior to". Running the payment patterns
over a termination clause finds the "30 days" in "within 30 days of
termination the Supplier shall return all materials" — a real number, in a
real sentence, that has nothing to do with the notice period.

So the patterns here are anchored on notice vocabulary, and the anchoring is
the family.

THE DIRECTION OF THE DEFAULT MATTERS
====================================

A notice-period assertion is almost always a FLOOR ("at least 60 days"),
because the risk is a supplier who can walk away next week. A payment-terms
assertion is almost always a CEILING. Neither family assumes it — the compiler
reads the comparator off the sentence — but it is why the two exist
separately: an administrator writing about notice and an administrator writing
about payment are protecting against opposite failures, and the review queue
groups by family.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Optional

from app.core.normalize import duration_days
from app.services.assertions import vocabulary as vocab
from app.services.assertions.families import (
    Chunk,
    FamilyReading,
    normalize_clause_text,
    sentence_around,
)
from app.services.assertions.families.duration_bound import satisfies

__all__ = ["FAMILY", "extract", "satisfies", "verdict_for"]

FAMILY: str = vocab.FAMILY_NOTICE_PERIOD

#: Words that make a nearby day count a NOTICE period rather than any other
#: period. Checked within the same sentence as the number.
_NOTICE_ANCHORS = re.compile(
    r"\b(?:notice|notify|notifying|terminate|termination|terminating|"
    r"cancel|cancellation|non-?renewal|renew)\b",
    re.IGNORECASE,
)

#: Anchors that mean the OPPOSITE of a notice period: a duty that begins after
#: termination, not a warning before it. A day count in one of these sentences
#: is excluded rather than scored down, because it is not weak evidence of a
#: notice period — it is evidence of something else.
_POST_TERMINATION = re.compile(
    r"\b(?:within\s+\d{1,4}\s*(?:\(\s*\d+\s*\)\s*)?(?:calendar\s+|business\s+|working\s+)?"
    r"days?\s+(?:of|after|following)\s+(?:the\s+)?(?:termination|expiry|expiration)"
    r"|upon\s+termination|after\s+termination|post-?termination)\b",
    re.IGNORECASE,
)

_NUMBER = re.compile(
    r"\b(?P<count>\d{1,4}|[a-z\-]+)\s*(?:\(\s*\d{1,4}\s*\)\s*)?"
    r"(?P<unit>calendar\s+days|business\s+days|working\s+days|days?|weeks?|months?)\b",
    re.IGNORECASE,
)

_WORD_NUMBERS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
    "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "forty-five": 45, "sixty": 60, "ninety": 90,
    "hundred": 100, "one hundred and eighty": 180,
}

_BUSINESS_DAY_PENALTY: float = 0.45


def _count(token: str) -> Optional[int]:
    text = (token or "").strip().lower()
    if text in _WORD_NUMBERS:
        return _WORD_NUMBERS[text]
    if text.isdigit():
        return int(text)
    return None


def extract(
    chunk: Chunk, *, plan: Any, workspace_currency: Optional[str] = None
) -> tuple[FamilyReading, ...]:
    body = normalize_clause_text(chunk.text)
    if not body:
        return ()

    found: list[FamilyReading] = []
    for match in _NUMBER.finditer(body):
        start, end = match.span()
        quote, span = sentence_around(body, start, end)
        if not _NOTICE_ANCHORS.search(quote):
            continue
        if _POST_TERMINATION.search(quote):
            continue

        count = _count(match.group("count"))
        if count is None:
            continue
        unit_word = match.group("unit").lower()

        notes: list[str] = []
        confidence = 0.90
        if "business" in unit_word or "working" in unit_word:
            confidence = min(confidence, _BUSINESS_DAY_PENALTY)
            notes.append(
                "the clause counts business days; this reading treats them as "
                "calendar days and needs a human to confirm"
            )

        days = duration_days(f"{count} {unit_word.split()[-1]}")
        if days is None:
            continue
        if "day" not in unit_word:
            notes.append(f"read \u201c{count} {unit_word}\u201d as {days} days")

        # "prior written notice" in the same sentence is the strongest signal
        # this product will ever get that a number is a notice period.
        if re.search(r"\bprior\s+written\s+notice\b", quote, re.IGNORECASE):
            confidence = min(0.96, confidence + 0.05)

        found.append(
            FamilyReading(
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                quote=quote,
                span=span,
                confidence=confidence,
                value=Decimal(days),
                unit=vocab.UNIT_DAYS,
                page_number=chunk.page_number,
                notes=tuple(notes),
            )
        )

    return tuple(found)


def verdict_for(readings: tuple[FamilyReading, ...], plan: Any) -> str:
    """PASS only when every notice period found satisfies the bound.

    Same ALL rule as `duration_bound`, for the same reason and with one extra
    edge: contracts routinely give the customer and the supplier DIFFERENT
    notice periods. "Either party may terminate on 90 days' notice; the
    Supplier may terminate on 30 days' notice for non-payment" contains both,
    and the shorter one is the exposure. Requiring every reading to satisfy the
    floor is what makes the shorter one decide the verdict.
    """
    if not readings:
        return vocab.VERDICT_UNDETERMINED
    if plan.bound is None:
        return vocab.VERDICT_UNDETERMINED

    bound = Decimal(str(plan.bound))
    for reading in readings:
        if reading.value is None:
            return vocab.VERDICT_UNDETERMINED
        if not satisfies(days=reading.value, operator=plan.operator, bound=bound):
            return vocab.VERDICT_FAIL
    return vocab.VERDICT_PASS