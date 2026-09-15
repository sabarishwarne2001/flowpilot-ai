"""ARCH-33 — `duration_bound`: payment terms and other day-counted limits.

WHAT IT READS
=============

    Net 30                                  30
    NET-45                                  45
    payable within thirty (30) days         30
    due within 45 days of receipt           45
    payment shall be made within 2 weeks    14
    Net 30 from receipt of a correct        30   (§4.9, deliberately)
      invoice
    due on receipt                           0

§4.9 is explicit that "Net 30 from receipt of a correct invoice" and "Net 30"
both compile to 30 days, and that a reviewer's correction is how the
distinction gets taught. This parser does not try to be cleverer than that,
because the alternative is a parser that sometimes reads the qualifier and
sometimes does not, which produces two different answers for one contract
depending on which chunk retrieval happened to rank first.

EVERY NUMBER GOES THROUGH `normalize.duration_days`
===================================================

`app/core/normalize.py` is the only parser for durations in this codebase, and
"Net 2 months" is 60 days there. Re-implementing the month arithmetic here
would give ARCH-33 a second opinion about how long a month is, and a
procurement case and an assertion evaluation over the same invoice would then
disagree about the same clause.

THE COMPARISON LIVES HERE
=========================

`satisfies` is not a shared helper. It is the semantics of a day bound, it is
where "Net 30 means 30 is acceptable" is written down, and it is the mutation
target `verify_arch33.py` inverts. A comparator inversion in a shared numeric
helper would break four families at once and be obvious; inverted HERE it
produces a rule that quietly passes every contract it was written to stop, and
only a gate that feeds it a 45-day clause against a 30-day bound can tell.
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

__all__ = ["FAMILY", "extract", "satisfies", "verdict_for"]

FAMILY: str = vocab.FAMILY_DURATION_BOUND

#: Patterns that carry a day count, in the order they are tried. `net` first,
#: because "Net 30 days" would otherwise match the bare-days pattern on the
#: same number and produce two readings of one clause, which the agreement
#: feature would then score as unanimous corroboration of itself.
_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    (
        "net_terms",
        re.compile(r"\bnet\s*[-/]?\s*(?P<count>\d{1,3})\b(?:\s*days?\b)?", re.IGNORECASE),
        0.95,
    ),
    (
        "within_words",
        re.compile(
            r"\bwithin\s+(?P<count>\d{1,4}|[a-z\-]+)\s*(?:\(\s*\d{1,4}\s*\)\s*)?"
            r"(?P<unit>calendar\s+days|business\s+days|working\s+days|days?|weeks?|months?)\b",
            re.IGNORECASE,
        ),
        0.92,
    ),
    (
        "payable_within",
        re.compile(
            r"\b(?:payable|due|paid|settled|remitted)\s+(?:in|within)\s+"
            r"(?P<count>\d{1,4}|[a-z\-]+)\s*(?:\(\s*\d{1,4}\s*\)\s*)?"
            r"(?P<unit>calendar\s+days|business\s+days|working\s+days|days?|weeks?|months?)\b",
            re.IGNORECASE,
        ),
        0.93,
    ),
    (
        "bare_days",
        re.compile(
            r"\b(?P<count>\d{1,4}|[a-z\-]+)\s*(?:\(\s*\d{1,4}\s*\)\s*)?"
            r"(?P<unit>calendar\s+days|business\s+days|working\s+days|days?|weeks?|months?)\b"
            r"(?=[^.;]{0,80}?\b(?:payment|invoice|payable|due|terms|settle)\b)",
            re.IGNORECASE,
        ),
        0.78,
    ),
    (
        "on_receipt",
        re.compile(
            r"\b(?:due\s+on\s+receipt|payable\s+on\s+receipt|cash\s+on\s+delivery|"
            r"immediately\s+upon\s+invoice)\b",
            re.IGNORECASE,
        ),
        0.90,
    ),
)

#: Business days are not calendar days, and a 30-business-day term is six
#: weeks. The parser reports the CALENDAR figure it would have to assume and
#: drops its confidence hard, so the routing rule sends it to a human instead
#: of silently converting. That is the §4.9 posture applied to a unit rather
#: than to a qualifier.
_BUSINESS_DAY_PENALTY: float = 0.45

_WORD_NUMBERS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fourteen": 14, "fifteen": 15, "twenty": 20,
    "thirty": 30, "forty": 40, "forty-five": 45, "sixty": 60, "ninety": 90,
}


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
    """Every day count in this chunk, each with the sentence it came from."""
    body = normalize_clause_text(chunk.text)
    if not body:
        return ()

    found: list[FamilyReading] = []
    claimed: list[tuple[int, int]] = []

    for label, pattern, base_confidence in _PATTERNS:
        for match in pattern.finditer(body):
            start, end = match.span()
            if any(start < c_end and c_start < end for c_start, c_end in claimed):
                continue

            notes: list[str] = []
            confidence = base_confidence

            if label == "on_receipt":
                days = 0
                notes.append("read as due on receipt (0 days)")
            else:
                count = _count(match.group("count"))
                if count is None:
                    continue
                unit_word = (
                    match.groupdict().get("unit") or "days"
                ).lower()
                if "business" in unit_word or "working" in unit_word:
                    confidence = min(confidence, _BUSINESS_DAY_PENALTY)
                    notes.append(
                        "the clause counts business days; this reading treats "
                        "them as calendar days and needs a human to confirm"
                    )
                # The one duration parser in the codebase.
                days = duration_days(f"{count} {unit_word.split()[-1]}")
                if days is None:
                    continue
                if not unit_word.startswith("day") and "day" not in unit_word:
                    notes.append(f"read \u201c{count} {unit_word}\u201d as {days} days")

            claimed.append((start, end))
            quote, span = sentence_around(body, start, end)
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


def satisfies(*, days: Decimal, operator: str, bound: Decimal) -> bool:
    """Does `days` satisfy `operator bound`?

    Both sides inclusive where the operator is inclusive: a contract with
    exactly Net 30 SATISFIES "payment terms do not exceed Net 30". Reading
    that as a strict `<` would fail every contract written to the policy the
    rule encodes, which is the population the rule exists to wave through.
    """
    if operator == vocab.OP_LE:
        return days <= bound
    if operator == vocab.OP_LT:
        return days < bound
    if operator == vocab.OP_GE:
        return days >= bound
    if operator == vocab.OP_GT:
        return days > bound
    if operator == vocab.OP_EQ:
        return days == bound
    raise ValueError(
        f"{operator!r} is not a comparison a day bound can make. The compiler "
        f"only emits {', '.join(vocab.NUMERIC_OPERATORS)} for this family."
    )


def verdict_for(readings: tuple[FamilyReading, ...], plan: Any) -> str:
    """PASS only when every reading satisfies the bound.

    ALL, not majority and not best-scoring. A contract whose terms clause says
    Net 30 and whose annex says Net 60 does not have payment terms of 30 days;
    it has an inconsistency, and the answer to an inconsistency is a human.
    Taking the highest-confidence reading would produce a confident PASS on a
    document that contains a clause the rule was written to catch.
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