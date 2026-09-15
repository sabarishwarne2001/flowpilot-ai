"""ARCH-33 — `money_bound`: an absolute amount or a rate.

    Late fee is at most 2% per month      -> late_fee.pct_month <= 2
    Late fee must not exceed Rs. 5,000    -> late_fee <= INR 5000 (micros)

TWO UNITS, ONE FAMILY, AND NEVER MIXED
======================================

A percentage and an amount are both "money bounds" to an administrator and are
not comparable to each other. 2% is not 2 rupees and no conversion exists
without knowing the principal, so a reading whose unit differs from the plan's
is UNDETERMINED rather than converted or compared.

That refusal is the entire safety property of this family. The failure it
prevents is specific: a rule written as "late fee is at most 2% per month"
meeting a contract that says "a late fee of Rs. 2,000 applies", and a parser
that compared the bare numbers and returned PASS.

THE PERIOD IS PART OF THE UNIT
==============================

`pct_month` and `pct_year` are different units, not a percentage with a
display suffix. 2% per month is 24% a year, which is the difference between an
ordinary late-payment term and one a procurement team wants stopped. So the
period is carried in the unit, mismatches are UNDETERMINED like any other unit
mismatch, and nothing here multiplies by twelve.

MONEY GOES THROUGH `normalize.money_micros`
===========================================

Integer millionths, never floats, and the Indian/EU/Western grouping question
is answered in exactly one place in this codebase. `1,23,456.78` on an Indian
invoice and `1.234.567,89` on a German one both arrive here as micros because
`normalize` already knows how to read them, and `workspace_currency` is passed
through for the genuinely ambiguous `1.234` case rather than guessed at.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Optional

from app.core.normalize import AmbiguousValue, NormalizationError, money_micros
from app.services.assertions import vocabulary as vocab
from app.services.assertions.families import (
    Chunk,
    FamilyReading,
    normalize_clause_text,
    sentence_around,
)

__all__ = ["FAMILY", "extract", "satisfies", "verdict_for"]

FAMILY: str = vocab.FAMILY_MONEY_BOUND

_PERCENT = re.compile(
    r"(?P<value>\d{1,3}(?:[.,]\d{1,4})?)\s*(?:%|per\s*cent|percent)"
    r"(?P<period>\s*(?:per|a|each|every|p\.?)\s*(?:day|month|mensem|year|annum))?",
    re.IGNORECASE,
)

_AMOUNT = re.compile(
    r"(?:(?:rs\.?|inr|usd|eur|gbp|sgd|aud|us\$)\s*|[\u20b9$\u20ac\u00a3\u00a5])"
    r"\s*(?P<amount>\d[\d,\.]*)",
    re.IGNORECASE,
)

_PERIOD_UNITS: tuple[tuple[str, str], ...] = (
    ("day", vocab.UNIT_PCT_DAY),
    ("month", vocab.UNIT_PCT_MONTH),
    ("mensem", vocab.UNIT_PCT_MONTH),
    ("annum", vocab.UNIT_PCT_YEAR),
    ("year", vocab.UNIT_PCT_YEAR),
)

#: Subjects the parser can anchor on. A percentage in a contract might be a
#: late fee, an escalation cap, a discount or a share of revenue, and the only
#: thing separating them is the surrounding words.
_SUBJECT_ANCHORS: dict[str, re.Pattern[str]] = {
    # Three alternatives, because a late-payment clause is written three
    # ways and only one of them leads with the word "late". "Interest at 3%
    # per month shall accrue on any invoice not paid when due" is the most
    # common drafting and contains neither "late" nor "overdue".
    "late_fee": re.compile(
        r"\b(?:late|overdue|past\s+due|delayed|default)\b[^.;]{0,60}?"
        r"\b(?:fee|charge|interest|payment|penalty)\b"
        r"|\binterest\b[^.;]{0,80}?"
        r"\b(?:overdue|late|unpaid|outstanding|not\s+paid\s+when\s+due|"
        r"past\s+due|after\s+the\s+due\s+date)\b"
        r"|\b(?:fee|charge|interest|penalty)\b[^.;]{0,80}?"
        r"\bnot\s+paid\s+when\s+due\b",
        re.IGNORECASE,
    ),
    "price_increase": re.compile(
        r"\b(?:increase|escalat|uplift|adjust)\w*\b[^.;]{0,60}?"
        r"\b(?:fees?|prices?|charges?|rates?)\b"
        r"|\b(?:fees?|prices?|charges?|rates?)\b[^.;]{0,40}?\b(?:increase|escalat)\w*\b",
        re.IGNORECASE,
    ),
    "discount": re.compile(r"\b(?:discount|rebate|reduction)\b", re.IGNORECASE),
    "deposit": re.compile(
        r"\b(?:security\s+)?deposit\b|\badvance\s+payment\b", re.IGNORECASE
    ),
    "liability_cap": re.compile(
        r"\b(?:liability|liable|indemnit\w*|cap(?:ped)?)\b", re.IGNORECASE
    ),
}

_EXPLICIT_PERIOD_CONFIDENCE: float = 0.92
#: A percentage with no stated period. Common ("interest of 2% on overdue
#: amounts") and genuinely ambiguous, so it is read at the rule's period with
#: a confidence low enough to route.
_IMPLIED_PERIOD_CONFIDENCE: float = 0.55
_AMOUNT_CONFIDENCE: float = 0.90
#: The parser found a number in the right kind of sentence but the unit does
#: not match the rule. Reported, not compared.
_UNIT_MISMATCH_CONFIDENCE: float = 0.35


def _anchor_for(subject: str) -> Optional[re.Pattern[str]]:
    return _SUBJECT_ANCHORS.get(subject)


def _period_unit(period: str) -> Optional[str]:
    lowered = (period or "").lower()
    for word, unit in _PERIOD_UNITS:
        if word in lowered:
            return unit
    return None


def extract(
    chunk: Chunk, *, plan: Any, workspace_currency: Optional[str] = None
) -> tuple[FamilyReading, ...]:
    body = normalize_clause_text(chunk.text)
    if not body:
        return ()

    anchor = _anchor_for(getattr(plan, "subject", "") or "")
    wanted_unit = getattr(plan, "unit", None)
    found: list[FamilyReading] = []

    for match in _PERCENT.finditer(body):
        quote, span = sentence_around(body, match.start(), match.end())
        if anchor is not None and not anchor.search(quote):
            continue

        raw = match.group("value").replace(",", ".")
        try:
            value = Decimal(raw)
        except Exception:  # noqa: BLE001 - malformed number, not a reading
            continue

        stated = _period_unit(match.group("period") or "")
        notes: list[str] = []
        if stated is not None:
            unit = stated
            confidence = _EXPLICIT_PERIOD_CONFIDENCE
        elif wanted_unit in (
            vocab.UNIT_PCT_DAY,
            vocab.UNIT_PCT_MONTH,
            vocab.UNIT_PCT_YEAR,
        ):
            unit = wanted_unit
            confidence = _IMPLIED_PERIOD_CONFIDENCE
            notes.append(
                "the clause states no period for this percentage; it has been "
                "read at the rule's period and needs a human to confirm"
            )
        else:
            unit = vocab.UNIT_PCT
            confidence = _EXPLICIT_PERIOD_CONFIDENCE

        if wanted_unit and unit != wanted_unit:
            confidence = min(confidence, _UNIT_MISMATCH_CONFIDENCE)
            notes.append(
                f"the clause states a rate per {unit.replace('pct_', '')} and "
                f"the rule is written per {str(wanted_unit).replace('pct_', '')}; "
                "these are not the same rate"
            )

        found.append(
            FamilyReading(
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                quote=quote,
                span=span,
                confidence=confidence,
                value=value,
                unit=unit,
                page_number=chunk.page_number,
                notes=tuple(notes),
            )
        )

    for match in _AMOUNT.finditer(body):
        quote, span = sentence_around(body, match.start(), match.end())
        if anchor is not None and not anchor.search(quote):
            continue

        try:
            money = money_micros(
                match.group(0), workspace_currency=workspace_currency
            )
        except AmbiguousValue:
            # `normalize` refused rather than guessing. That refusal is the
            # right behaviour and it is reported as a low-confidence absence
            # of a reading, not swallowed: an amount nobody could read is
            # exactly what the review queue is for.
            continue
        except NormalizationError:
            continue

        notes = []
        confidence = _AMOUNT_CONFIDENCE
        if wanted_unit and wanted_unit != vocab.UNIT_MICROS:
            confidence = _UNIT_MISMATCH_CONFIDENCE
            notes.append(
                "the clause states an amount and the rule states a "
                "percentage; there is no conversion between them without the "
                "principal"
            )
        if money.currency_from_workspace:
            notes.append(
                "the clause did not name a currency; the workspace setting "
                "was used"
            )

        found.append(
            FamilyReading(
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                quote=quote,
                span=span,
                confidence=confidence,
                value=Decimal(money.micros),
                unit=vocab.UNIT_MICROS,
                literal=money.currency,
                page_number=chunk.page_number,
                notes=tuple(notes),
            )
        )

    return tuple(found)


def satisfies(*, amount: Decimal, operator: str, bound: Decimal) -> bool:
    """Compare two values already known to share a unit."""
    if operator == vocab.OP_LE:
        return amount <= bound
    if operator == vocab.OP_LT:
        return amount < bound
    if operator == vocab.OP_GE:
        return amount >= bound
    if operator == vocab.OP_GT:
        return amount > bound
    if operator == vocab.OP_EQ:
        return amount == bound
    raise ValueError(f"{operator!r} is not a comparison a money bound can make.")


def verdict_for(readings: tuple[FamilyReading, ...], plan: Any) -> str:
    """PASS only when every same-unit reading satisfies the bound.

    A unit mismatch is UNDETERMINED for the whole set, not skipped. Skipping
    it would let a document that states a rate in the rule's unit AND an
    amount in another pass on the half the parser understood, which is a
    confident answer built on ignoring half the evidence.
    """
    if not readings:
        return vocab.VERDICT_UNDETERMINED
    if plan.bound is None:
        return vocab.VERDICT_UNDETERMINED

    bound = Decimal(str(plan.bound))
    wanted_unit = getattr(plan, "unit", None)

    for reading in readings:
        if reading.value is None:
            return vocab.VERDICT_UNDETERMINED
        if wanted_unit and reading.unit != wanted_unit:
            return vocab.VERDICT_UNDETERMINED
        if not satisfies(
            amount=reading.value, operator=plan.operator, bound=bound
        ):
            return vocab.VERDICT_FAIL
    return vocab.VERDICT_PASS