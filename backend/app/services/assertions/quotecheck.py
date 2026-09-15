"""ARCH-33 §4.3 — "Why the LLM path demands a quote."

THE FAILURE THIS CONVERTS
=========================

The most common way a language model is wrong about a document is not that it
refuses or contradicts itself. It is a fluent, specific, confident answer
about text that is not on the page. Nothing downstream of the model can tell
that apart from a correct answer by reading the answer.

So the model is required to return the exact sentence it relied on, and this
module checks that the sentence is actually in the chunks that were retrieved.
An answer whose quote is not found is treated as confidence 0 and triaged. The
hallucination becomes a routing decision instead of a wrong pass.

WHAT "VERBATIM" MEANS HERE
==========================

Strict byte equality would fail on every real document. A PDF text layer
carries soft hyphens, non-breaking spaces, ligatures, line breaks inside
sentences and double spaces after full stops; a model reproducing the sentence
normalises most of that without being asked. Demanding the bytes back would
reject correct answers constantly, and a check that fires on correct answers
gets turned off.

So the comparison is on NORMALISED WHITESPACE — §4.3's own words — plus the
same Unicode folding the parsers use. What is deliberately NOT normalised:

  * letters and digits. "30 days" never matches "thirty days".
  * word order.
  * punctuation that carries meaning: a comma inside a number stays.

The check is lenient about how text was rendered and strict about what it
says. That is the only split that makes it both usable and load-bearing.

A MINIMUM LENGTH, AND WHY
=========================

A three-word quote is in every contract. If a model can satisfy the check with
"the Supplier shall", the check is decoration. `MIN_QUOTE_CHARS` is the length
below which a match stops being evidence that the model read the clause.

PURE
====

Standard library only. `verify_arch33.py` drives the refusal path offline, and
the mutation suite bypasses the check to prove a gate notices.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional, Sequence

__all__ = [
    "QuoteVerdict",
    "normalize_for_quote",
    "verify_quote",
    "MIN_QUOTE_CHARS",
    "UNGROUNDED_CONFIDENCE",
]

#: Below this, a match is a coincidence rather than evidence.
MIN_QUOTE_CHARS: int = 24

#: What an unverified quote forces. Zero, not "low" — §4.3 says confidence 0,
#: and the difference matters: a small positive number multiplied through a
#: feature vector can still clear a threshold on an otherwise strong signal.
UNGROUNDED_CONFIDENCE: float = 0.0

_WHITESPACE = re.compile(r"\s+")
#: Soft hyphen, zero-width space, zero-width non-joiner, word joiner, BOM.
_INVISIBLE = re.compile(r"[\u00ad\u200b\u200c\u2060\ufeff]")
#: A hyphen at a line break: "termina-\ntion" is one word.
_HYPHEN_BREAK = re.compile(r"-\s*\n\s*")


@dataclass(frozen=True)
class QuoteVerdict:
    """Whether the model's quote is actually in the retrieved text."""

    grounded: bool
    #: The chunk the quote was found in. None when it was not found.
    chunk_id: Optional[str] = None
    chunk_index: Optional[int] = None
    page_number: Optional[int] = None
    #: Offsets into the chunk's NORMALISED text. Not the original text: the
    #: normalisation collapses runs of whitespace and therefore moves
    #: positions, and reporting an original-text span here would be a number
    #: that looks precise and is wrong. The review queue highlights by
    #: searching for the quote, not by these offsets.
    normalized_span: Optional[tuple[int, int]] = None
    reason: str = ""

    @property
    def confidence(self) -> float:
        """0.0 when ungrounded. See `UNGROUNDED_CONFIDENCE`."""
        return 1.0 if self.grounded else UNGROUNDED_CONFIDENCE

    def as_evidence(self) -> dict[str, Any]:
        return {
            "grounded": self.grounded,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "page_number": self.page_number,
            "normalized_span": (
                None if self.normalized_span is None else list(self.normalized_span)
            ),
            "reason": self.reason,
        }


def normalize_for_quote(text: str) -> str:
    """Fold rendering artefacts, preserve every letter and digit.

    NOT length-preserving, unlike `families.normalize_clause_text`. This
    function exists to compare two strings, not to report offsets into one, so
    it is free to collapse whitespace — which is the entire point.
    """
    body = text or ""
    body = _HYPHEN_BREAK.sub("", body)
    body = _INVISIBLE.sub("", body)
    body = unicodedata.normalize("NFKC", body)
    body = (
        body.replace("\u00a0", " ")
        .replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )
    body = _WHITESPACE.sub(" ", body)
    return body.strip().casefold()


def verify_quote(quote: Optional[str], chunks: Sequence[Any]) -> QuoteVerdict:
    """Is `quote` present in any of `chunks`?

    `chunks` are `families.Chunk` values, or anything with `.text`,
    `.chunk_id`, `.chunk_index` and `.page_number`. Typed loosely on purpose:
    this module must not import the families package, because the families
    package must never be a dependency of the LLM path — the whole point of
    the LLM family is that no parser understood the sentence.
    """
    raw = (quote or "").strip()
    if not raw:
        return QuoteVerdict(
            grounded=False,
            reason=(
                "The model returned no quote. An answer with nothing to check "
                "it against cannot be distinguished from an invented one."
            ),
        )

    needle = normalize_for_quote(raw)
    if len(needle) < MIN_QUOTE_CHARS:
        return QuoteVerdict(
            grounded=False,
            reason=(
                f"The quote is {len(needle)} characters after normalisation, "
                f"below the {MIN_QUOTE_CHARS} needed for a match to be "
                "evidence rather than a coincidence."
            ),
        )

    for chunk in chunks:
        haystack = normalize_for_quote(getattr(chunk, "text", "") or "")
        if not haystack:
            continue
        position = haystack.find(needle)
        if position == -1:
            continue
        return QuoteVerdict(
            grounded=True,
            chunk_id=getattr(chunk, "chunk_id", None),
            chunk_index=getattr(chunk, "chunk_index", None),
            page_number=getattr(chunk, "page_number", None),
            normalized_span=(position, position + len(needle)),
            reason="",
        )

    return QuoteVerdict(
        grounded=False,
        reason=(
            "The sentence the model says it relied on does not appear in any "
            "of the paragraphs retrieved from this document. The answer is "
            "about text that is not there, so it is routed for review rather "
            "than acted on."
        ),
    )