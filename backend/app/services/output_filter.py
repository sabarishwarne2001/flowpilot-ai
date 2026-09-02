"""ARCH-12 Step 4 — output filtering at the token buffer (A5).

WHY THE BUFFER AND NOT THE TOKEN
================================

A redaction that runs on one token at a time cannot see a card number split
across three of them. Providers tokenise on subword boundaries, so
`4111 1111 1111 1111` arrives as something like `411`, `1 1111`, ` 1111 11`,
`11`. Every one of those fragments passes a card-number regex. The
concatenation does not.

`StreamRedactor` therefore holds back a tail. `feed()` accumulates, matches
against the accumulated text, and returns only the prefix that is far enough
from the end that no further token could extend a match into it. `flush()`
releases the remainder at end of stream.

The held-back window is `LOOKBEHIND` characters. It is sized against the
longest pattern this module can match plus formatting slack, not chosen
round — a window shorter than the longest pattern is a filter with a
guaranteed bypass, and the bypass is "put the identifier at the end of the
answer".

WHY LUHN
========

A 16-digit run is usually an order number, an invoice reference or a
timestamp concatenation. Redacting all of them makes the assistant useless on
exactly the documents this product exists to read. Luhn is two lines and it
removes almost all of that false-positive class.

WHY PROMPT-ECHO FILTERING SITS HERE TOO
=======================================

The fence markers `context_assembly_service` emits carry a per-request nonce.
A model that echoes `<<<SOURCE-a1b2c3` back into the answer is telling the
user — and anyone the transcript is forwarded to — the exact delimiter
structure needed to construct an injection that survives the next assembly.
The nonce makes that echo detectable with certainty rather than heuristically,
which is why the redactor is constructed with it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Final, Iterable, Pattern

logger = logging.getLogger("app.services.output_filter")

#: Held-back tail, in characters. Longest matchable pattern is an IBAN at 34
#: characters; the window is comfortably above it so that separators and
#: markdown emphasis inserted mid-identifier cannot push a match past the
#: boundary.
LOOKBEHIND: Final[int] = 96

REDACTION: Final[str] = "[redacted]"


@dataclass(frozen=True)
class RedactionRule:
    name: str
    pattern: Pattern[str]
    #: Optional post-match validator. Returning False leaves the text alone.
    validator: str | None = None


def _luhn_ok(digits: str) -> bool:
    stripped = [int(char) for char in digits if char.isdigit()]
    if len(stripped) < 13:
        return False
    checksum = 0
    parity = len(stripped) % 2
    for index, digit in enumerate(stripped):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


#: Ordered. Earlier rules win on overlapping spans, so the specific
#: identifiers precede the generic digit runs.
RULES: tuple[RedactionRule, ...] = (
    RedactionRule(
        "api_key",
        re.compile(r"\b(?:fp_live_|fp_test_|sk-|ghp_|xox[baprs]-)[A-Za-z0-9_\-]{8,}"),
    ),
    RedactionRule(
        "card_number",
        re.compile(r"\b(?:\d[ \-]?){12,18}\d\b"),
        validator="luhn",
    ),
    RedactionRule(
        "iban",
        re.compile(r"\b[A-Z]{2}\d{2}[ ]?(?:[A-Z0-9]{4}[ ]?){2,7}[A-Z0-9]{1,4}\b"),
    ),
    RedactionRule(
        "aadhaar",
        re.compile(r"\b\d{4}[ \-]?\d{4}[ \-]?\d{4}\b"),
        validator="not_year_run",
    ),
    RedactionRule("pan_in", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    RedactionRule("ssn_us", re.compile(r"\b(?!000|666|9\d\d)\d{3}-\d{2}-\d{4}\b")),
    RedactionRule("ni_uk", re.compile(r"\b[A-Z]{2}\d{6}[A-D]\b")),
    RedactionRule(
        "email",
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ),
    RedactionRule(
        "phone",
        re.compile(
            r"(?<![\w.])(?:"
            r"\+\d{1,3}[ \-]?\(?\d{1,5}\)?[ \-]?\d{3,5}[ \-]?\d{3,5}"
            r"|"
            r"\(?\d{2,4}\)?[ \-]\d{3,4}[ \-]\d{3,4}"
            r"|"
            r"\b\d{3}[ \-]\d{4}\b"
            r")(?![\w.])"
        ),
    ),
)

#: Structural leakage. These are never legitimate assistant output.
PROMPT_ECHO_PATTERNS: tuple[Pattern[str], ...] = (
    re.compile(r"<<<SOURCE-[0-9a-f]{4,}", re.I),
    re.compile(r"SOURCE-[0-9a-f]{4,}>>>", re.I),
    re.compile(r"\[/?INST\]|<\|im_(?:start|end)\|>", re.I),
    re.compile(r"^\s*={8,}\s*$", re.M),
    re.compile(
        r"={4,}\s*(?:Document Context|Conversation History|Task-Specific "
        r"Instructions|Evidence & Citation Guidance|Context Usage Guidance)\s*={4,}",
        re.I,
    ),
    re.compile(
        r"\bYou are FlowPilot AI, an enterprise document intelligence assistant\b",
        re.I,
    ),
)


def _validator_ok(rule: RedactionRule, matched: str) -> bool:
    if rule.validator == "luhn":
        return _luhn_ok(matched)
    if rule.validator == "not_year_run":
        # 2024 2025 2026 is a table of years, not an Aadhaar number.
        groups = re.findall(r"\d{4}", matched)
        return not all(1900 <= int(group) <= 2100 for group in groups)
    return True


@dataclass
class RedactionTally:
    counts: dict[str, int] = field(default_factory=dict)

    def hit(self, name: str) -> None:
        self.counts[name] = self.counts.get(name, 0) + 1

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def as_details(self) -> dict[str, object]:
        return {"redactions": self.total, "by_rule": dict(self.counts)}


def redact_text(
    text: str,
    *,
    fence_nonce: str | None = None,
    tally: RedactionTally | None = None,
) -> str:
    """Apply every rule to a complete string.

    Used by the notification dispatcher (Step 7) and by the non-streaming
    path. `StreamRedactor` below is the incremental form of the same rules —
    they share this function so the two can never drift.
    """
    counter = tally or RedactionTally()
    result = text

    for rule in RULES:
        def _replace(match: re.Match[str]) -> str:
            if not _validator_ok(rule, match.group(0)):
                return match.group(0)
            counter.hit(rule.name)
            return REDACTION

        result = rule.pattern.sub(_replace, result)

    for pattern in PROMPT_ECHO_PATTERNS:
        result, count = pattern.subn("", result)
        if count:
            counter.hit("prompt_echo")

    if fence_nonce:
        nonce_pattern = re.compile(re.escape(fence_nonce), re.I)
        result, count = nonce_pattern.subn("", result)
        if count:
            counter.hit("fence_nonce")

    return result


class StreamRedactor:
    """Incremental redaction over a token stream.

    Usage:

        redactor = StreamRedactor(fence_nonce=fenced.fence_nonce)
        for chunk in provider_stream:
            safe = redactor.feed(chunk.text)
            if safe:
                yield sse("token", safe)
        tail = redactor.flush()

    `emitted_text` accumulates the redacted output, which is what gets
    persisted — the stored transcript and the delivered transcript are the
    same bytes, so a user scrolling back never sees something the filter
    removed in flight.
    """

    def __init__(self, *, fence_nonce: str | None = None) -> None:
        self._fence_nonce = fence_nonce or None
        self._pending = ""
        self._emitted_parts: list[str] = []
        self.tally = RedactionTally()

    @property
    def emitted_text(self) -> str:
        """Everything released so far, post-redaction."""
        return "".join(self._emitted_parts)

    def feed(self, chunk: str) -> str:
        """Absorb a token; return the portion now safe to send."""
        if not chunk:
            return ""
        self._pending += chunk

        if len(self._pending) <= LOOKBEHIND:
            return ""

        cut = len(self._pending) - LOOKBEHIND
        # Never split inside a run of word characters — doing so is exactly
        # how a filter loses a match that straddles the cut.
        while cut > 0 and (self._pending[cut - 1].isalnum() or self._pending[cut - 1] in "-_+@."):
            cut -= 1
        if cut <= 0:
            return ""

        head, self._pending = self._pending[:cut], self._pending[cut:]
        return self._release(head)

    def flush(self) -> str:
        """Release whatever is still held. Call once, at end of stream."""
        head, self._pending = self._pending, ""
        if not head:
            return ""
        return self._release(head)

    def _release(self, text: str) -> str:
        safe = redact_text(text, fence_nonce=self._fence_nonce, tally=self.tally)
        self._emitted_parts.append(safe)
        return safe

    def log_summary(self, **extra: object) -> None:
        if self.tally.total:
            logger.warning(
                "stream.output_redacted", extra={**self.tally.as_details(), **extra}
            )


def scan(text: str) -> dict[str, int]:
    """Report what would be redacted without redacting. For tests and audits."""
    tally = RedactionTally()
    redact_text(text, tally=tally)
    return dict(tally.counts)


__all__ = [
    "LOOKBEHIND",
    "PROMPT_ECHO_PATTERNS",
    "REDACTION",
    "RULES",
    "RedactionRule",
    "RedactionTally",
    "StreamRedactor",
    "redact_text",
    "scan",
]
