"""ARCH-33 §4.2/§4.3 — the compiler. Sentence in, typed plan out.

WHAT THIS MODULE IS FOR
=======================

§4.2 is the whole product decision: "It is not an open-ended 'ask the AI
anything' condition." Every assertion compiles to a typed check with a known
evaluation method, and the rule builder shows the compiled form BEFORE the
rule can be saved. This module is that compile step, and it runs at save time
in the request cycle — not at run time — so that an administrator finds out
their sentence was not understood while they are still looking at it.

Falling through to the LLM family is a legitimate outcome, not a failure. It
is labelled, it requires a confirmation tick, and it carries a stated reason
so the author can see WHY their sentence was not typed and reword it if they
want the deterministic path. `CompileOutcome.reason` is that sentence, in
plain words, and it is shown to a human.

PURE
====

No Session, no clock, no settings lookup, no I/O. `verify_arch33.py` drives
the truth table below without a database. The only import outside the standard
library is `app.core.normalize`, which is itself pure and is the ONLY parser
for numbers, dates, durations and money in this codebase.

WHY A GRAMMAR AND NOT A REGEX PER SENTENCE
==========================================

The tempting implementation is one regex per example sentence in §4.3's table.
It passes the table and nothing else: "Payment terms do not exceed Net 30"
matches and "Payment terms shall not exceed 30 days" does not, and the author
of the second sentence gets the LLM path with no idea why.

So instead: a comparator table (longest match wins), a bound parser that reads
the value KIND, a per-family subject synonym table, and negation handling that
flips a strict operator into its inclusive complement. The family is chosen by
the pair (value kind, subject), which is what makes "Termination notice is at
least 60 days" a `notice_period` and "Payment terms are at least 30 days" a
`duration_bound` despite identical shape.

THE ORDER OF THE ATTEMPTS IS PART OF THE CONTRACT
=================================================

1. quantitative (a comparator with a bound)
2. absence   (a negated existence claim)
3. presence  (an existence claim)
4. enumerated (a membership claim)
5. llm

Absence is tried BEFORE presence, and that ordering is the single most
consequential line in this file. "The contract does not include an automatic
renewal clause" contains the word "include". A presence-first compiler types
it as `exists(clause: auto renewal)`, which is the exact inverse of what the
administrator wrote, and every document that fails the rule passes it. An
inverted assertion is worse than an uncompiled one, because the uncompiled one
goes to a human.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from app.core.normalize import NormalizationError, duration_days, money_micros
from app.services.assertions import vocabulary as vocab

__all__ = [
    "AssertionPlan",
    "CompileOutcome",
    "CompileError",
    "compile_sentence",
    "MAX_SENTENCE_LENGTH",
]

#: Long enough for any clause assertion a human writes; short enough that the
#: `sentence` column and the console's single-line input agree about what fits.
MAX_SENTENCE_LENGTH: int = 400


class CompileError(ValueError):
    """The sentence cannot be compiled AND cannot be handed to the LLM either.

    Only raised for input that is not an assertion at all — empty, or longer
    than `MAX_SENTENCE_LENGTH`. An ordinary sentence the grammar does not
    understand returns an `llm` plan with a reason; it does not raise. The
    distinction matters because one of them is a validation error the console
    shows next to the field, and the other is a normal product outcome.
    """


# ===========================================================================
# The plan
# ===========================================================================


@dataclass(frozen=True)
class AssertionPlan:
    """A typed check. This is what `assertion_definitions.plan` holds.

    Frozen because a plan is compiled once, digested, and stored. A mutable
    plan is a plan that can be edited after its digest was taken, which makes
    the digest a description of something that no longer exists.
    """

    family: str
    #: Canonical subject key: `payment_terms`, `liability_cap`, `governing_law`.
    #: For presence/absence this is the clause key, e.g. `auto_renewal`.
    subject: str
    operator: str
    #: None for EXISTS / NOT_EXISTS / IN. A Decimal for every numeric operator.
    bound: Optional[Decimal] = None
    unit: Optional[str] = None
    #: For `enumerated`: the permitted values, already canonicalised.
    values: tuple[str, ...] = ()
    #: For `money_multiple_bound`: what the multiple is OF (`annual_value`).
    basis: Optional[str] = None
    #: For `money_bound` with an absolute amount: the currency that was read
    #: off the sentence. None means a bare number or a percentage.
    currency: Optional[str] = None
    #: Human words for the clause an existence check is about: "auto renewal".
    clause_display: Optional[str] = None
    #: Set only when `family == 'llm'`. Why the grammar did not type it.
    reason: Optional[str] = None
    #: Phrases retrieval should search for, beyond the family seeds.
    retrieval_seeds: tuple[str, ...] = ()
    sentence: str = ""
    engine_version: str = vocab.ENGINE_VERSION

    # -- serialisation ----------------------------------------------------

    def as_json(self) -> dict[str, Any]:
        """Canonical dict for the `plan` jsonb column and for the digest.

        Decimals are serialised as STRINGS, not floats. `Decimal("1.0")` and
        `Decimal("1")` render as different strings and the same float, and the
        difference is meaningful here: `1.0 x annual value` was written with
        one decimal place and `1 x` was not. More importantly a float round
        trip through JSON can change the value, and this dict is hashed.
        """
        payload: dict[str, Any] = {
            "engine_version": self.engine_version,
            "family": self.family,
            "subject": self.subject,
            "operator": self.operator,
            "bound": None if self.bound is None else str(self.bound),
            "unit": self.unit,
            "values": list(self.values),
            "basis": self.basis,
            "currency": self.currency,
            "clause_display": self.clause_display,
            "reason": self.reason,
            "retrieval_seeds": list(self.retrieval_seeds),
        }
        return payload

    def input_digest(self, *, settings: Optional[dict[str, Any]] = None) -> str:
        """SHA-256 over the canonical plan plus the rule settings.

        The `input_digest` pattern from ARCH-31: everything that could change
        the answer goes in, so that a re-evaluation under a changed threshold
        or a bumped engine is genuinely new work and a repeat under identical
        inputs is not. `ENGINE_VERSION` is inside `as_json()`, so a parser fix
        invalidates every digest it could have affected.
        """
        payload = {"plan": self.as_json(), "settings": settings or {}}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    # -- display ----------------------------------------------------------

    def describe(self) -> str:
        """The console's "Understood as:" line, in the compact §4.3 form.

        Deliberately compact rather than chatty. §4.6 puts this next to the
        sentence the administrator just typed; repeating their words back in
        a full sentence reads as agreement, while `payment_terms.days <= 30`
        reads as an INTERPRETATION they can check.
        """
        if self.family == vocab.FAMILY_LLM:
            return "Checked by the AI model, billed as assistant usage"

        if self.operator == vocab.OP_EXISTS:
            return f"exists(clause: {self.clause_display or self.subject})"
        if self.operator == vocab.OP_NOT_EXISTS:
            return f"not exists(clause: {self.clause_display or self.subject})"
        if self.operator == vocab.OP_IN:
            inside = ", ".join(self.values)
            symbol = vocab.OPERATOR_SYMBOLS[vocab.OP_IN]
            return f"{self.subject} {symbol} {{{inside}}}"

        symbol = vocab.OPERATOR_SYMBOLS[self.operator]
        bound = "" if self.bound is None else _plain(self.bound)

        if self.unit == vocab.UNIT_MULTIPLE:
            # Rendered with at least one decimal place. "1 x annual value" and
            # "1.0 x annual value" mean the same thing and do not READ the
            # same: the bare integer looks like a count of contracts, and the
            # decimal reads as the factor it is. §4.3's table shows "1.0".
            factor = bound if "." in bound else f"{bound}.0"
            return f"{self.subject} {symbol} {factor} \u00d7 {self.basis}"
        if self.unit == vocab.UNIT_DAYS:
            return f"{self.subject}.days {symbol} {bound}"
        if self.unit in (
            vocab.UNIT_PCT,
            vocab.UNIT_PCT_DAY,
            vocab.UNIT_PCT_MONTH,
            vocab.UNIT_PCT_YEAR,
        ):
            return f"{self.subject}.{self.unit} {symbol} {bound}"
        if self.unit == vocab.UNIT_MICROS and self.bound is not None:
            amount = _plain(self.bound / Decimal(1_000_000))
            currency = f"{self.currency} " if self.currency else ""
            return f"{self.subject} {symbol} {currency}{amount}"
        return f"{self.subject} {symbol} {bound}"


@dataclass(frozen=True)
class CompileOutcome:
    """A plan and everything the console needs to render the block."""

    plan: AssertionPlan
    #: `DETERMINISTIC` or `LLM`, derived from the family. Never passed in.
    evaluation_mode: str
    #: True when the author must tick the LLM confirmation before saving.
    requires_acknowledgement: bool
    #: Present for the LLM family. Plain words, shown to a human.
    reason: Optional[str] = None
    #: Non-fatal observations: "read 'Net 30' as 30 days", and so on. The
    #: console renders them under the Understood-as line.
    notes: tuple[str, ...] = field(default_factory=tuple)


def _plain(value: Decimal) -> str:
    """Render a Decimal without an exponent and without trailing noise."""
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


# ===========================================================================
# Text preparation
# ===========================================================================

_WORD_NUMBERS: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "forty-five": 45,
    "fortyfive": 45,
    "sixty": 60,
    "ninety": 90,
}


def _fold(value: str) -> str:
    """NFKD, drop combining marks, keep currency symbols and the times sign."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _prepare(sentence: str) -> str:
    """Lower-cased, whitespace-collapsed, trailing punctuation removed.

    Curly quotes and non-breaking spaces are folded, because a sentence pasted
    out of a Word document carries both and an administrator cannot see the
    difference between the space they typed and the one they pasted.
    """
    text = _fold(sentence or "")
    text = text.replace("\u00a0", " ").replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip(".;: ")
    return text.lower()


# ===========================================================================
# Comparators
# ===========================================================================


@dataclass(frozen=True)
class _Comparator:
    phrase: str
    operator: str
    #: True when the phrase already carries its own negation, so the generic
    #: negation scan must not flip it a second time. "does not exceed" is
    #: `LE` because of the "not"; flipping it again yields `GT`, which is the
    #: inverse of the rule and passes every document that should fail.
    self_negating: bool = False


#: Longest phrase first at match time. `no more than` must be found before
#: `more than`, or every "no more than N" compiles to `> N`.
_COMPARATORS: tuple[_Comparator, ...] = (
    _Comparator("shall not exceed", vocab.OP_LE, self_negating=True),
    _Comparator("does not exceed", vocab.OP_LE, self_negating=True),
    _Comparator("do not exceed", vocab.OP_LE, self_negating=True),
    _Comparator("must not exceed", vocab.OP_LE, self_negating=True),
    _Comparator("may not exceed", vocab.OP_LE, self_negating=True),
    _Comparator("not to exceed", vocab.OP_LE, self_negating=True),
    _Comparator("cannot exceed", vocab.OP_LE, self_negating=True),
    _Comparator("no more than", vocab.OP_LE, self_negating=True),
    _Comparator("not more than", vocab.OP_LE, self_negating=True),
    _Comparator("no greater than", vocab.OP_LE, self_negating=True),
    _Comparator("not greater than", vocab.OP_LE, self_negating=True),
    _Comparator("no longer than", vocab.OP_LE, self_negating=True),
    _Comparator("no later than", vocab.OP_LE, self_negating=True),
    _Comparator("less than or equal to", vocab.OP_LE),
    _Comparator("at most", vocab.OP_LE),
    _Comparator("maximum of", vocab.OP_LE),
    _Comparator("a maximum of", vocab.OP_LE),
    _Comparator("capped at", vocab.OP_LE),
    _Comparator("limited to", vocab.OP_LE),
    _Comparator("up to", vocab.OP_LE),
    _Comparator("within", vocab.OP_LE),
    _Comparator("no less than", vocab.OP_GE, self_negating=True),
    _Comparator("not less than", vocab.OP_GE, self_negating=True),
    _Comparator("no shorter than", vocab.OP_GE, self_negating=True),
    _Comparator("not shorter than", vocab.OP_GE, self_negating=True),
    _Comparator("greater than or equal to", vocab.OP_GE),
    _Comparator("at least", vocab.OP_GE),
    _Comparator("minimum of", vocab.OP_GE),
    _Comparator("a minimum of", vocab.OP_GE),
    _Comparator("or more", vocab.OP_GE),
    _Comparator("greater than", vocab.OP_GT),
    _Comparator("more than", vocab.OP_GT),
    _Comparator("longer than", vocab.OP_GT),
    _Comparator("exceeds", vocab.OP_GT),
    _Comparator("exceed", vocab.OP_GT),
    _Comparator("less than", vocab.OP_LT),
    _Comparator("fewer than", vocab.OP_LT),
    _Comparator("shorter than", vocab.OP_LT),
    _Comparator("exactly", vocab.OP_EQ),
    _Comparator("equal to", vocab.OP_EQ),
    _Comparator("equals", vocab.OP_EQ),
)

_COMPARATORS_BY_LENGTH: tuple[_Comparator, ...] = tuple(
    sorted(_COMPARATORS, key=lambda c: -len(c.phrase))
)

#: Words that negate a comparator that does not negate itself.
_NEGATIONS: tuple[str, ...] = ("not", "never", "no", "n't", "cannot", "neither")

#: How far back from the comparator a negation is allowed to sit. Three words
#: covers "is not", "shall never be", "must not be"; widening it would let the
#: "no" in "no fault of the supplier" ten words earlier flip an operator.
_NEGATION_WINDOW_WORDS: int = 3

#: Flipping a STRICT operator gives its INCLUSIVE complement and vice versa.
#: `not (x > 30)` is `x <= 30`, not `x < 30`.
_FLIP: dict[str, str] = {
    vocab.OP_GT: vocab.OP_LE,
    vocab.OP_LE: vocab.OP_GT,
    vocab.OP_LT: vocab.OP_GE,
    vocab.OP_GE: vocab.OP_LT,
}


@dataclass(frozen=True)
class _Match:
    comparator: _Comparator
    start: int
    end: int
    operator: str
    flipped: bool


def _word_boundary_find(haystack: str, needle: str, start: int = 0) -> int:
    """`str.find` that refuses a match inside a longer word.

    "within" inside "notwithstanding" is the case this exists for, and it is
    not hypothetical: "notwithstanding the foregoing" opens a large fraction
    of the clauses this product reads.
    """
    index = haystack.find(needle, start)
    while index != -1:
        before_ok = index == 0 or not haystack[index - 1].isalnum()
        after = index + len(needle)
        after_ok = after >= len(haystack) or not haystack[after].isalnum()
        if before_ok and after_ok:
            return index
        index = haystack.find(needle, index + 1)
    return -1


def _is_negated(text: str, comparator_start: int) -> bool:
    """Whether a negation word sits within the window before the comparator."""
    prefix = text[:comparator_start].strip()
    if not prefix:
        return False
    words = prefix.split()[-_NEGATION_WINDOW_WORDS:]
    return any(word.strip(",;:") in _NEGATIONS for word in words)


def _find_comparators(text: str) -> list[_Match]:
    """Every comparator in the sentence, left to right, longest match wins.

    Overlaps are resolved by claiming the character span: once "no more than"
    has claimed its span, the "more than" inside it cannot match.
    """
    claimed: list[tuple[int, int]] = []
    matches: list[_Match] = []

    for comparator in _COMPARATORS_BY_LENGTH:
        cursor = 0
        while True:
            index = _word_boundary_find(text, comparator.phrase, cursor)
            if index == -1:
                break
            end = index + len(comparator.phrase)
            overlaps = any(index < c_end and c_start < end for c_start, c_end in claimed)
            if not overlaps:
                claimed.append((index, end))
                flipped = False
                operator = comparator.operator
                if not comparator.self_negating and _is_negated(text, index):
                    replacement = _FLIP.get(operator)
                    if replacement is not None:
                        operator = replacement
                        flipped = True
                matches.append(
                    _Match(
                        comparator=comparator,
                        start=index,
                        end=end,
                        operator=operator,
                        flipped=flipped,
                    )
                )
            cursor = index + 1

    return sorted(matches, key=lambda m: m.start)


# ===========================================================================
# Bound values
# ===========================================================================

_MULTIPLE = re.compile(
    r"^\s*(?P<factor>\d+(?:\.\d+)?|[a-z\-]+)\s*(?:x|\u00d7|times)\s+(?P<basis>.+)$"
)
_PERCENT = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?:%|percent|per\s*cent)"
    r"(?P<period>\s*(?:per|a|each|every)\s*(?:day|month|year|annum))?"
)
_NET_TERMS = re.compile(r"\bnet\s*[-/]?\s*(\d{1,3})\b")
_DURATION = re.compile(
    r"\b(?P<count>\d{1,4}|[a-z\-]+)\s*(?:\((?:\d{1,4})\)\s*)?"
    r"(?P<unit>calendar\s+days|business\s+days|working\s+days|days?|weeks?|months?)\b"
)
_CURRENCY_HINT = re.compile(r"[\u20b9$\u20ac\u00a3\u00a5]|\b(?:inr|usd|eur|gbp|rs\.?)\b")

#: Bases a multiple can be taken OF. The key is what a contract says; the
#: value is the canonical basis name that appears in the compiled plan and
#: that `money_multiple_bound` resolves against the document.
_MULTIPLE_BASES: tuple[tuple[str, str], ...] = (
    ("annual contract value", "annual_value"),
    ("annual value", "annual_value"),
    ("annual fees", "annual_value"),
    ("annual charges", "annual_value"),
    ("yearly contract value", "annual_value"),
    ("total contract value", "contract_value"),
    ("contract value", "contract_value"),
    ("fees paid", "fees_paid"),
    ("amounts paid", "fees_paid"),
    ("charges paid", "fees_paid"),
    ("the fees", "fees_paid"),
    ("monthly fees", "monthly_fees"),
)

_PERIOD_UNITS: dict[str, str] = {
    "day": vocab.UNIT_PCT_DAY,
    "month": vocab.UNIT_PCT_MONTH,
    "year": vocab.UNIT_PCT_YEAR,
    "annum": vocab.UNIT_PCT_YEAR,
}


@dataclass(frozen=True)
class _Bound:
    value: Decimal
    unit: str
    basis: Optional[str] = None
    currency: Optional[str] = None
    note: Optional[str] = None


def _number(token: str) -> Optional[Decimal]:
    """A digit run or an English number word, as a Decimal."""
    text = token.strip().lower()
    if not text:
        return None
    if text in _WORD_NUMBERS:
        return Decimal(_WORD_NUMBERS[text])
    cleaned = text.replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _read_bound(text: str) -> Optional[_Bound]:
    """Read the value on the right of a comparator, and say what KIND it is.

    Order is significant and is gated:

      multiple  before  money, because "1x annual contract value" contains no
                       currency but "2 times $50,000" contains one, and a
                       money-first reader would take the 50,000 and drop the
                       multiplier.
      percent   before  money, because "2%" is a bare number to a money parser
                       and would be read as two micro-units of currency.
      money     before  duration, because "30 days" and "$30" both contain 30
                       and only one of them is a period of time.
    """
    candidate = text.strip().strip(".,;:")
    if not candidate:
        return None

    multiple = _MULTIPLE.match(candidate)
    if multiple:
        factor = _number(multiple.group("factor"))
        if factor is not None:
            tail = multiple.group("basis").strip()
            for phrase, basis in _MULTIPLE_BASES:
                if phrase in tail:
                    return _Bound(
                        value=factor, unit=vocab.UNIT_MULTIPLE, basis=basis
                    )
            # A multiplier with a basis the table does not know. Deliberately
            # NOT defaulted to annual_value: "2x the deposit" and "2x annual
            # value" differ by an order of magnitude on a real contract, and
            # guessing produces a number a reviewer cannot audit.
            return None

    percent = _PERCENT.search(candidate)
    if percent:
        value = _number(percent.group("value").replace(",", "."))
        if value is not None:
            unit = vocab.UNIT_PCT
            period = (percent.group("period") or "").strip()
            for word, mapped in _PERIOD_UNITS.items():
                if word in period:
                    unit = mapped
                    break
            return _Bound(value=value, unit=unit)

    if _CURRENCY_HINT.search(candidate):
        try:
            money = money_micros(candidate)
        except NormalizationError:
            money = None
        if money is not None:
            return _Bound(
                value=Decimal(money.micros),
                unit=vocab.UNIT_MICROS,
                currency=None if money.currency == "XXX" else money.currency,
            )

    net = _NET_TERMS.search(candidate)
    if net:
        return _Bound(
            value=Decimal(int(net.group(1))),
            unit=vocab.UNIT_DAYS,
            note=f"read \u201c{net.group(0)}\u201d as {int(net.group(1))} days",
        )

    duration = _DURATION.search(candidate)
    if duration:
        count = _number(duration.group("count"))
        if count is not None:
            unit_word = duration.group("unit")
            # `normalize.duration_days` is the ONLY duration parser in this
            # codebase, so the arithmetic goes through it rather than being
            # repeated here with a second opinion about how long a month is.
            days = duration_days(f"{count} {unit_word.split()[-1]}")
            if days is not None:
                note = None
                if not unit_word.startswith("day"):
                    note = (
                        f"read \u201c{count} {unit_word}\u201d as {days} days"
                    )
                return _Bound(
                    value=Decimal(days), unit=vocab.UNIT_DAYS, note=note
                )

    bare = _number(candidate.split()[0]) if candidate.split() else None
    if bare is not None:
        # A bare number with no unit. Returned WITHOUT a unit guess: the
        # family decision below refuses it, because "payment terms at most 30"
        # is thirty days to a human and thirty of something to a parser, and a
        # parser that picks is a parser that will eventually pick wrong on a
        # sentence about money.
        return _Bound(value=bare, unit="")

    return None


# ===========================================================================
# Subjects
# ===========================================================================

#: `(phrase, canonical_subject, family)`. Longest phrase wins, so "payment
#: terms" is tried before "terms" would be if "terms" were ever listed.
_SUBJECTS: tuple[tuple[str, str, str], ...] = (
    # duration_bound
    ("payment terms", "payment_terms", vocab.FAMILY_DURATION_BOUND),
    ("payment term", "payment_terms", vocab.FAMILY_DURATION_BOUND),
    ("terms of payment", "payment_terms", vocab.FAMILY_DURATION_BOUND),
    ("payment period", "payment_terms", vocab.FAMILY_DURATION_BOUND),
    ("net terms", "payment_terms", vocab.FAMILY_DURATION_BOUND),
    ("invoice due date", "payment_terms", vocab.FAMILY_DURATION_BOUND),
    ("payment due", "payment_terms", vocab.FAMILY_DURATION_BOUND),
    ("delivery lead time", "delivery_lead_time", vocab.FAMILY_DURATION_BOUND),
    ("lead time", "delivery_lead_time", vocab.FAMILY_DURATION_BOUND),
    ("cure period", "cure_period", vocab.FAMILY_DURATION_BOUND),
    ("remedy period", "cure_period", vocab.FAMILY_DURATION_BOUND),
    ("warranty period", "warranty_period", vocab.FAMILY_DURATION_BOUND),
    # notice_period
    ("termination notice", "termination_notice", vocab.FAMILY_NOTICE_PERIOD),
    ("notice of termination", "termination_notice", vocab.FAMILY_NOTICE_PERIOD),
    ("termination for convenience notice", "termination_notice", vocab.FAMILY_NOTICE_PERIOD),
    ("notice period", "termination_notice", vocab.FAMILY_NOTICE_PERIOD),
    ("prior written notice", "termination_notice", vocab.FAMILY_NOTICE_PERIOD),
    ("written notice", "termination_notice", vocab.FAMILY_NOTICE_PERIOD),
    ("renewal notice", "renewal_notice", vocab.FAMILY_NOTICE_PERIOD),
    ("non-renewal notice", "renewal_notice", vocab.FAMILY_NOTICE_PERIOD),
    # money_multiple_bound / money_bound share their subjects; the VALUE KIND
    # decides which family, not the subject.
    ("aggregate liability", "liability_cap", vocab.FAMILY_MONEY_MULTIPLE_BOUND),
    ("limitation of liability", "liability_cap", vocab.FAMILY_MONEY_MULTIPLE_BOUND),
    ("liability cap", "liability_cap", vocab.FAMILY_MONEY_MULTIPLE_BOUND),
    ("cap on liability", "liability_cap", vocab.FAMILY_MONEY_MULTIPLE_BOUND),
    ("total liability", "liability_cap", vocab.FAMILY_MONEY_MULTIPLE_BOUND),
    ("liability", "liability_cap", vocab.FAMILY_MONEY_MULTIPLE_BOUND),
    ("indemnity cap", "indemnity_cap", vocab.FAMILY_MONEY_MULTIPLE_BOUND),
    ("indemnification cap", "indemnity_cap", vocab.FAMILY_MONEY_MULTIPLE_BOUND),
    # money_bound
    ("late payment interest", "late_fee", vocab.FAMILY_MONEY_BOUND),
    ("interest on late payment", "late_fee", vocab.FAMILY_MONEY_BOUND),
    ("interest on overdue amounts", "late_fee", vocab.FAMILY_MONEY_BOUND),
    ("late fee", "late_fee", vocab.FAMILY_MONEY_BOUND),
    ("late charge", "late_fee", vocab.FAMILY_MONEY_BOUND),
    ("penalty interest", "late_fee", vocab.FAMILY_MONEY_BOUND),
    ("security deposit", "deposit", vocab.FAMILY_MONEY_BOUND),
    ("deposit", "deposit", vocab.FAMILY_MONEY_BOUND),
    ("annual price increase", "price_increase", vocab.FAMILY_MONEY_BOUND),
    ("price increase", "price_increase", vocab.FAMILY_MONEY_BOUND),
    ("uplift", "price_increase", vocab.FAMILY_MONEY_BOUND),
    ("discount", "discount", vocab.FAMILY_MONEY_BOUND),
    # enumerated
    ("governing law", "governing_law", vocab.FAMILY_ENUMERATED),
    ("applicable law", "governing_law", vocab.FAMILY_ENUMERATED),
    ("law of the contract", "governing_law", vocab.FAMILY_ENUMERATED),
    ("choice of law", "governing_law", vocab.FAMILY_ENUMERATED),
    ("exclusive jurisdiction", "jurisdiction", vocab.FAMILY_ENUMERATED),
    ("jurisdiction", "jurisdiction", vocab.FAMILY_ENUMERATED),
    ("venue", "jurisdiction", vocab.FAMILY_ENUMERATED),
    ("billing currency", "currency", vocab.FAMILY_ENUMERATED),
    ("invoice currency", "currency", vocab.FAMILY_ENUMERATED),
    ("currency", "currency", vocab.FAMILY_ENUMERATED),
)

_SUBJECTS_BY_LENGTH: tuple[tuple[str, str, str], ...] = tuple(
    sorted(_SUBJECTS, key=lambda row: -len(row[0]))
)

#: Noise a subject phrase collects. Stripped before the synonym lookup so
#: "the total aggregate liability of the supplier" resolves.
_SUBJECT_NOISE = re.compile(
    r"\b(?:the|a|an|our|their|its|any|all|total|overall|supplier's|vendor's|"
    r"customer's|contractor's|this|that|agreement's|contract's|shall|must|"
    r"will|should|is|are|be|being|of|for|under|in|on)\b"
)


def _resolve_subject(phrase: str) -> Optional[tuple[str, str]]:
    """`(canonical_subject, preferred_family)` for a subject phrase."""
    text = re.sub(r"\s+", " ", (phrase or "").strip().lower())
    if not text:
        return None
    for needle, subject, family in _SUBJECTS_BY_LENGTH:
        if _word_boundary_find(text, needle) != -1:
            return subject, family
    return None


# ===========================================================================
# Clause keys, for presence and absence
# ===========================================================================

#: `(phrase, clause_key, display, extra retrieval phrases)`.
_CLAUSES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "data processing",
        "data_processing",
        "data processing",
        ("data processing addendum", "processing of personal data", "sub-processor"),
    ),
    (
        "data protection addendum",
        "data_processing",
        "data processing",
        ("data processing addendum", "processing of personal data"),
    ),
    ("dpa", "data_processing", "data processing", ("data processing addendum",)),
    (
        "automatic renewal",
        "auto_renewal",
        "auto renewal",
        ("automatically renew", "renewal term", "successive periods", "evergreen"),
    ),
    (
        "auto renewal",
        "auto_renewal",
        "auto renewal",
        ("automatically renew", "renewal term", "successive periods"),
    ),
    (
        "auto-renewal",
        "auto_renewal",
        "auto renewal",
        ("automatically renew", "renewal term"),
    ),
    (
        "automatically renew",
        "auto_renewal",
        "auto renewal",
        ("automatic renewal", "renewal term"),
    ),
    (
        "evergreen",
        "auto_renewal",
        "auto renewal",
        ("automatic renewal", "successive periods"),
    ),
    (
        "limitation of liability",
        "liability_limitation",
        "limitation of liability",
        ("aggregate liability", "shall not exceed", "in no event"),
    ),
    (
        "confidentiality",
        "confidentiality",
        "confidentiality",
        ("confidential information", "non-disclosure", "shall keep confidential"),
    ),
    (
        "non-disclosure",
        "confidentiality",
        "confidentiality",
        ("confidential information", "shall keep confidential"),
    ),
    (
        "force majeure",
        "force_majeure",
        "force majeure",
        ("acts of god", "beyond the reasonable control"),
    ),
    (
        "indemnity",
        "indemnity",
        "indemnity",
        ("indemnify", "hold harmless", "defend"),
    ),
    (
        "indemnification",
        "indemnity",
        "indemnity",
        ("indemnify", "hold harmless"),
    ),
    (
        "arbitration",
        "arbitration",
        "arbitration",
        ("arbitral tribunal", "seat of arbitration", "arbitration act"),
    ),
    (
        "audit right",
        "audit_rights",
        "audit rights",
        ("right to audit", "upon reasonable notice", "books and records"),
    ),
    (
        "audit rights",
        "audit_rights",
        "audit rights",
        ("right to audit", "books and records"),
    ),
    (
        "non-solicit",
        "non_solicit",
        "non-solicitation",
        ("shall not solicit", "non-solicitation"),
    ),
    (
        "non-compete",
        "non_compete",
        "non-compete",
        ("shall not compete", "restraint of trade"),
    ),
    (
        "assignment",
        "assignment",
        "assignment",
        ("may not assign", "prior written consent", "change of control"),
    ),
    (
        "termination for convenience",
        "termination_for_convenience",
        "termination for convenience",
        ("terminate for convenience", "without cause"),
    ),
    (
        "service level",
        "service_levels",
        "service levels",
        ("service level agreement", "uptime", "service credits"),
    ),
    (
        "insurance",
        "insurance",
        "insurance",
        ("shall maintain insurance", "certificate of insurance", "coverage"),
    ),
)

_CLAUSES_BY_LENGTH = tuple(sorted(_CLAUSES, key=lambda row: -len(row[0])))


def _resolve_clause(text: str) -> Optional[tuple[str, str, tuple[str, ...]]]:
    """`(clause_key, display, extra_seeds)` for an existence claim."""
    for needle, key, display, seeds in _CLAUSES_BY_LENGTH:
        if _word_boundary_find(text, needle) != -1:
            return key, display, seeds
    return None


# ===========================================================================
# Enumerated values
# ===========================================================================

#: Jurisdictions, as ISO 3166-1 alpha-2 where one exists. The compiled plan
#: holds the CODE — §4.3's table compiles "India or Singapore" to `{IN, SG}` —
#: because the document may say "the Republic of India" and the comparison has
#: to be against one canonical value, not against whichever spelling the
#: administrator happened to use.
_ENUM_VALUES: tuple[tuple[str, str], ...] = (
    ("united kingdom", "GB"),
    ("england and wales", "GB"),
    ("england", "GB"),
    ("united states", "US"),
    ("united states of america", "US"),
    ("usa", "US"),
    ("delaware", "US-DE"),
    ("new york", "US-NY"),
    ("california", "US-CA"),
    ("india", "IN"),
    ("republic of india", "IN"),
    ("singapore", "SG"),
    ("australia", "AU"),
    ("canada", "CA"),
    ("germany", "DE"),
    ("france", "FR"),
    ("netherlands", "NL"),
    ("ireland", "IE"),
    ("switzerland", "CH"),
    ("japan", "JP"),
    ("uae", "AE"),
    ("united arab emirates", "AE"),
    ("hong kong", "HK"),
    ("inr", "INR"),
    ("usd", "USD"),
    ("eur", "EUR"),
    ("gbp", "GBP"),
    ("sgd", "SGD"),
    ("rupees", "INR"),
    ("dollars", "USD"),
    ("euros", "EUR"),
)

_ENUM_BY_LENGTH = tuple(sorted(_ENUM_VALUES, key=lambda row: -len(row[0])))

#: "between 30 and 60 days". Two limits in one sentence.
_BETWEEN = re.compile(r"\bbetween\b[^.]*?\band\b")

_ENUM_LEAD = re.compile(
    r"\b(?:is|are|must\s+be|shall\s+be|should\s+be|will\s+be|has\s+to\s+be|"
    r"is\s+one\s+of|must\s+be\s+one\s+of|is\s+either)\b"
)
_ENUM_SPLIT = re.compile(r"\s*(?:,|;|\bor\b|\band\b|/)\s*")


def _resolve_enumerated(tail: str) -> tuple[str, ...]:
    """Canonical values from "India or Singapore", "either IN, SG"."""
    cleaned = tail.strip().strip(".,;:")
    cleaned = re.sub(r"^\s*(?:either|one of|any of|the)\s+", "", cleaned)
    found: list[str] = []
    for part in _ENUM_SPLIT.split(cleaned):
        token = part.strip().strip(".,;:").lower()
        if not token:
            continue
        # Words that would otherwise be read as a value. "law" in "the law of
        # India" and "courts" in "the courts of Singapore".
        token = re.sub(
            r"^(?:the\s+)?(?:laws?|courts?|jurisdiction|state|republic)\s+of\s+",
            "",
            token,
        )
        token = token.strip()
        match = next(
            (code for needle, code in _ENUM_BY_LENGTH if _word_boundary_find(token, needle) != -1),
            None,
        )
        if match is not None and match not in found:
            found.append(match)
    return tuple(found)


# ===========================================================================
# Existence markers
# ===========================================================================

#: Checked BEFORE the presence markers. See the module header.
_ABSENCE_MARKERS: tuple[str, ...] = (
    "there is no",
    "there must be no",
    "there shall be no",
    "there are no",
    "does not include",
    "do not include",
    "does not contain",
    "do not contain",
    "must not include",
    "must not contain",
    "shall not include",
    "shall not contain",
    "may not include",
    "must not have",
    "shall not have",
    "does not have",
    "must be free of",
    "free from any",
    "without any",
    "contains no",
    "has no",
    "no automatic",
    "is not subject to",
)

_PRESENCE_MARKERS: tuple[str, ...] = (
    "includes",
    "include",
    "contains",
    "contain",
    "must include",
    "must contain",
    "shall include",
    "shall contain",
    "must have",
    "shall have",
    "has a",
    "has an",
    "there is a",
    "there is an",
    "is present",
    "is subject to",
    "provides for",
)


# ===========================================================================
# The compiler
# ===========================================================================


def compile_sentence(sentence: str) -> CompileOutcome:
    """Compile one sentence into a typed plan, or into the LLM family.

    Never raises for an ordinary sentence. `CompileError` is reserved for
    input that is not an assertion at all.
    """
    raw = (sentence or "").strip()
    if not raw:
        raise CompileError(
            "An assertion needs a sentence. Saving an empty one would "
            "produce a node that routes every document to triage with no "
            "explanation a reviewer could act on."
        )
    if len(raw) > MAX_SENTENCE_LENGTH:
        raise CompileError(
            f"An assertion sentence is limited to {MAX_SENTENCE_LENGTH} "
            f"characters; this one is {len(raw)}. A paragraph is not one "
            "check — split it into separate steps so each one has its own "
            "verdict and its own paragraph in the review queue."
        )

    text = _prepare(raw)
    notes: list[str] = []

    for attempt in (
        _try_quantitative,
        _try_absence,
        _try_presence,
        _try_enumerated,
    ):
        outcome = attempt(text, raw, notes)
        if outcome is not None:
            return outcome

    return _llm_outcome(
        raw,
        reason=(
            "No comparison, no clause and no list of permitted values could "
            "be identified in this sentence, so there is no typed check to "
            "compile it into."
        ),
    )


def _try_quantitative(
    text: str, raw: str, notes: list[str]
) -> Optional[CompileOutcome]:
    # "between 30 and 60 days" carries two limits and no comparator phrase, so
    # it would otherwise fall through to the LLM family with the generic
    # "nothing recognised" reason. It IS recognised; it is just two checks.
    # Saying so is the difference between an author rewording the sentence and
    # an author accepting an AI-evaluated rule they did not want.
    if _BETWEEN.search(text):
        return _llm_outcome(
            raw,
            reason=(
                "This sentence sets both an upper and a lower limit. An "
                "assertion step carries one comparison, so split it into two "
                "steps \u2014 each one then gets its own verdict and its own "
                "paragraph in the review queue."
            ),
        )

    matches = _find_comparators(text)
    if not matches:
        return None

    first, last = matches[0], matches[-1]

    # Two comparators pulling in opposite directions is a sentence with two
    # checks in it ("between 30 and 60 days"), and this phase ships one check
    # per node. Refusing is right; guessing which half the author meant is not.
    directions = {
        vocab.OP_LE: "upper",
        vocab.OP_LT: "upper",
        vocab.OP_GE: "lower",
        vocab.OP_GT: "lower",
        vocab.OP_EQ: "exact",
    }
    if len({directions[m.operator] for m in matches}) > 1:
        return _llm_outcome(
            raw,
            reason=(
                "This sentence sets both an upper and a lower limit. An "
                "assertion step carries one comparison, so split it into two "
                "steps \u2014 each one then gets its own verdict and its own "
                "paragraph in the review queue."
            ),
        )

    subject_phrase = text[: first.start]
    bound_text = text[last.end:]

    bound = _read_bound(bound_text)
    if bound is None:
        return _llm_outcome(
            raw,
            reason=(
                "A comparison was recognised but the value being compared "
                "against could not be read as a duration, an amount, a "
                "percentage or a multiple."
            ),
        )

    resolved = _resolve_subject(subject_phrase)
    if resolved is None:
        return _llm_outcome(
            raw,
            reason=(
                "The thing being measured was not recognised. Assertions "
                "compile against known subjects such as payment terms, "
                "termination notice, liability or a late fee."
            ),
        )
    subject, preferred_family = resolved

    if bound.unit == "":
        return _llm_outcome(
            raw,
            reason=(
                "The value has no unit, so it is not clear whether it means "
                "days, a percentage or an amount. Add the unit \u2014 for "
                "example \u201c30 days\u201d or \u201c2%\u201d."
            ),
        )

    if bound.note:
        notes.append(bound.note)
    if last.flipped:
        notes.append(
            "read the negation as \u201c"
            f"{vocab.OPERATOR_SYMBOLS[last.operator]}\u201d"
        )

    # The family is decided by the pair (value kind, subject). This is the
    # line that separates `duration_bound` from `notice_period` on two
    # sentences with identical grammar.
    if bound.unit == vocab.UNIT_MULTIPLE:
        family = vocab.FAMILY_MONEY_MULTIPLE_BOUND
    elif bound.unit in (
        vocab.UNIT_PCT,
        vocab.UNIT_PCT_DAY,
        vocab.UNIT_PCT_MONTH,
        vocab.UNIT_PCT_YEAR,
        vocab.UNIT_MICROS,
    ):
        family = vocab.FAMILY_MONEY_BOUND
    elif bound.unit == vocab.UNIT_DAYS:
        family = (
            vocab.FAMILY_NOTICE_PERIOD
            if preferred_family == vocab.FAMILY_NOTICE_PERIOD
            else vocab.FAMILY_DURATION_BOUND
        )
    else:  # pragma: no cover - guarded by the unit check above
        return _llm_outcome(raw, reason="The value's unit was not understood.")

    plan = AssertionPlan(
        family=family,
        subject=subject,
        operator=last.operator,
        bound=bound.value,
        unit=bound.unit,
        basis=bound.basis,
        currency=bound.currency,
        retrieval_seeds=_subject_seeds(subject),
        sentence=raw,
    )
    return CompileOutcome(
        plan=plan,
        evaluation_mode=vocab.mode_for_family(family),
        requires_acknowledgement=False,
        notes=tuple(notes),
    )


def _try_absence(text: str, raw: str, notes: list[str]) -> Optional[CompileOutcome]:
    if not any(_word_boundary_find(text, marker) != -1 for marker in _ABSENCE_MARKERS):
        return None
    clause = _resolve_clause(text)
    if clause is None:
        return _llm_outcome(
            raw,
            reason=(
                "This reads as \u201cthe document must not contain\u2026\u201d "
                "but the clause being excluded was not recognised, so there "
                "is nothing specific to search the document for."
            ),
        )
    key, display, seeds = clause
    plan = AssertionPlan(
        family=vocab.FAMILY_ABSENCE,
        subject=key,
        operator=vocab.OP_NOT_EXISTS,
        clause_display=display,
        retrieval_seeds=seeds,
        sentence=raw,
    )
    return CompileOutcome(
        plan=plan,
        evaluation_mode=vocab.MODE_DETERMINISTIC,
        requires_acknowledgement=False,
        notes=tuple(notes),
    )


def _try_presence(text: str, raw: str, notes: list[str]) -> Optional[CompileOutcome]:
    if not any(_word_boundary_find(text, marker) != -1 for marker in _PRESENCE_MARKERS):
        return None
    clause = _resolve_clause(text)
    if clause is None:
        return _llm_outcome(
            raw,
            reason=(
                "This reads as \u201cthe document must contain\u2026\u201d but "
                "the clause being required was not recognised, so there is "
                "nothing specific to search the document for."
            ),
        )
    key, display, seeds = clause
    plan = AssertionPlan(
        family=vocab.FAMILY_PRESENCE,
        subject=key,
        operator=vocab.OP_EXISTS,
        clause_display=display,
        retrieval_seeds=seeds,
        sentence=raw,
    )
    return CompileOutcome(
        plan=plan,
        evaluation_mode=vocab.MODE_DETERMINISTIC,
        requires_acknowledgement=False,
        notes=tuple(notes),
    )


def _try_enumerated(
    text: str, raw: str, notes: list[str]
) -> Optional[CompileOutcome]:
    lead = _ENUM_LEAD.search(text)
    if lead is None:
        return None

    resolved = _resolve_subject(text[: lead.start()])
    if resolved is None or resolved[1] != vocab.FAMILY_ENUMERATED:
        return None

    values = _resolve_enumerated(text[lead.end():])
    if not values:
        return _llm_outcome(
            raw,
            reason=(
                "The permitted values were not recognised as places or "
                "currencies, so the check has no list to compare against."
            ),
        )

    subject = resolved[0]
    plan = AssertionPlan(
        family=vocab.FAMILY_ENUMERATED,
        subject=subject,
        operator=vocab.OP_IN,
        values=values,
        retrieval_seeds=_subject_seeds(subject),
        sentence=raw,
    )
    return CompileOutcome(
        plan=plan,
        evaluation_mode=vocab.MODE_DETERMINISTIC,
        requires_acknowledgement=False,
        notes=tuple(notes),
    )


def _llm_outcome(raw: str, *, reason: str) -> CompileOutcome:
    """The labelled fall-through. Never silent, always acknowledged.

    `requires_acknowledgement=True` is what §4.2 means by "saved only as an
    explicitly LLM-evaluated assertion, labeled as such". The API refuses to
    persist a definition in this mode without `llm_acknowledged_by`, and
    `ck_ad_llm_acknowledged` refuses the row underneath it, so a console that
    forgot to render the tick cannot produce one either.
    """
    plan = AssertionPlan(
        family=vocab.FAMILY_LLM,
        subject="document",
        operator=vocab.OP_EXISTS,
        reason=reason,
        retrieval_seeds=_sentence_seeds(raw),
        sentence=raw,
    )
    return CompileOutcome(
        plan=plan,
        evaluation_mode=vocab.MODE_LLM,
        requires_acknowledgement=True,
        reason=reason,
    )


#: Retrieval phrases derived from the canonical subject, on top of the family
#: seeds. Scoped per subject rather than per family because "liability" and
#: "indemnity cap" share a family and share almost no vocabulary.
_SUBJECT_SEEDS: dict[str, tuple[str, ...]] = {
    "payment_terms": ("payment terms", "payable within", "days of receipt of invoice"),
    "delivery_lead_time": ("delivery", "lead time", "shall deliver within"),
    "cure_period": ("cure", "remedy the breach", "within which to cure"),
    "warranty_period": ("warranty", "warrants that", "warranty period"),
    "termination_notice": ("written notice", "terminate this agreement", "notice period"),
    "renewal_notice": ("notice of non-renewal", "renewal term", "notify the other party"),
    "liability_cap": ("aggregate liability", "shall not exceed", "in no event shall"),
    "indemnity_cap": ("indemnify", "indemnification", "shall not exceed"),
    "late_fee": ("late payment", "interest", "overdue", "per month"),
    "deposit": ("deposit", "shall pay a deposit", "refundable"),
    "price_increase": ("increase the fees", "price increase", "uplift", "annually"),
    "discount": ("discount", "rebate", "reduction in the fees"),
    "governing_law": ("governed by", "laws of", "governing law"),
    "jurisdiction": ("exclusive jurisdiction", "courts of", "venue"),
    "currency": ("denominated in", "payable in", "currency"),
}

_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "any", "are", "as", "at", "be", "by", "do", "does",
        "for", "from", "has", "have", "in", "is", "it", "its", "must", "no",
        "not", "of", "on", "or", "our", "shall", "should", "than", "that",
        "the", "their", "there", "this", "to", "we", "will", "with",
    }
)


def _subject_seeds(subject: str) -> tuple[str, ...]:
    return _SUBJECT_SEEDS.get(subject, ())


def _sentence_seeds(raw: str) -> tuple[str, ...]:
    """Content words from the sentence itself, for the LLM family only.

    A typed family has a subject to seed from. The LLM family does not — that
    is what made it fall through — so its only retrieval signal is the
    administrator's own words with the grammar removed.
    """
    words = [
        word.strip(".,;:()\"'")
        for word in _prepare(raw).split()
        if len(word) > 2 and word.strip(".,;:()\"'") not in _STOPWORDS
    ]
    seen: list[str] = []
    for word in words:
        if word and word not in seen:
            seen.append(word)
    return tuple(seen[:12])