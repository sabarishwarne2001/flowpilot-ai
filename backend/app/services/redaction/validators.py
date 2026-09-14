"""ARCH-32 — checksum validators for structured identifiers. Pure, stdlib only.

WHAT A VALIDATOR IS FOR HERE
============================

A pattern finds candidates. A validator decides which of them are real. That
distinction carries the phase: a 16-digit run is a purchase order number about
as often as it is a card, and redacting every 16-digit run on an invoice
destroys the document. Every function below answers one question — "does this
string satisfy the arithmetic its issuer guarantees?" — and nothing else.

They are consequently the only part of detection that can be tested
exhaustively against published truth, which is why `verify_arch32.py` carries
table-driven KNOWN-VALID and KNOWN-INVALID fixtures for all six families
rather than a smoke test per family. A validator that returns True for
everything passes a smoke test and redacts an entire page.

WHY EVERY FUNCTION TAKES THE RAW MATCH
======================================

Callers pass the text as it appeared in the document, separators and all.
Normalisation is each validator's own business because the families disagree
about it: spaces are meaningless inside an Aadhaar and meaningful inside an
IBAN's printed form, and a hyphen is structural in a US SSN and noise in a
card number. Pushing a single `strip_separators()` into the caller would
force one answer onto six different rules.

WHY THERE IS NO `validate(detector, text)` DISPATCHER
=====================================================

There is one, at the bottom, and it is a dict of the six functions rather than
an if-chain — because `detect.py` needs to iterate detectors generically and
`verify_arch32.py` needs to assert that every checksum detector in the
vocabulary has a validator registered. A detector added to the vocabulary with
no validator would otherwise silently accept every pattern match.

NOTHING HERE LOGS, AND NOTHING HERE RAISES ON BAD INPUT
=======================================================

These functions receive PII. A log line, an exception message carrying the
input, or a traceback would put the exact string this phase exists to destroy
into a log aggregator with a different retention policy. They return False.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

__all__ = [
    "luhn_ok",
    "validate_card_number",
    "card_brand",
    "verhoeff_ok",
    "validate_aadhaar",
    "validate_pan_india",
    "gstin_check_character",
    "validate_gstin",
    "validate_us_ssn",
    "iban_mod97",
    "validate_iban",
    "VALIDATORS",
    "validator_for",
]

# ---------------------------------------------------------------------------
# Card numbers — Luhn plus an IIN range
# ---------------------------------------------------------------------------

_CARD_SEPARATORS = re.compile(r"[ \-\u00a0\u2013\u2014]")


def luhn_ok(digits: str) -> bool:
    """ISO/IEC 7812 check digit. `digits` must already be digits only."""
    if not digits.isdigit() or len(digits) < 2:
        return False
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        value = ord(char) - 48
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


#: (low, high, length-set, brand). Ranges are compared on the numeric prefix of
#: the same width as the bound, which is how the card networks publish them.
_IIN_RANGES: tuple[tuple[str, str, frozenset[int], str], ...] = (
    ("4", "4", frozenset({13, 16, 19}), "visa"),
    ("51", "55", frozenset({16}), "mastercard"),
    ("2221", "2720", frozenset({16}), "mastercard"),
    ("34", "34", frozenset({15}), "amex"),
    ("37", "37", frozenset({15}), "amex"),
    ("6011", "6011", frozenset({16, 19}), "discover"),
    ("644", "649", frozenset({16, 19}), "discover"),
    ("65", "65", frozenset({16, 19}), "discover"),
    ("3528", "3589", frozenset({16, 17, 18, 19}), "jcb"),
    ("300", "305", frozenset({14, 16, 19}), "diners"),
    ("3095", "3095", frozenset({14, 16, 19}), "diners"),
    ("36", "36", frozenset({14, 16, 19}), "diners"),
    ("38", "39", frozenset({14, 16, 19}), "diners"),
    ("62", "62", frozenset({16, 17, 18, 19}), "unionpay"),
    ("81", "81", frozenset({16}), "rupay"),
    ("6521", "6522", frozenset({16}), "rupay"),
    ("50", "50", frozenset({12, 13, 14, 15, 16, 17, 18, 19}), "maestro"),
    ("56", "58", frozenset({12, 13, 14, 15, 16, 17, 18, 19}), "maestro"),
)


def card_brand(raw: str) -> Optional[str]:
    """The issuing scheme, or None if the prefix belongs to no known range.

    The brand is never stored. It exists so the studio can say "card number"
    with a straight face and so `verify_arch32.py` can assert that an IIN
    outside every published range is rejected even when Luhn passes — which is
    the case that separates a card from a Luhn-satisfying invoice number.
    """
    digits = _CARD_SEPARATORS.sub("", raw)
    if not digits.isdigit():
        return None
    length = len(digits)
    for low, high, lengths, brand in _IIN_RANGES:
        width = len(low)
        if length < width or length not in lengths:
            continue
        prefix = digits[:width]
        if low <= prefix <= high:
            return brand
    return None


def validate_card_number(raw: str) -> bool:
    """Luhn AND a published IIN range AND a length that range permits.

    All three, deliberately. Luhn alone accepts roughly one in ten arbitrary
    digit runs, and one in ten of the 16-digit numbers on a purchase order is
    far too many pages to destroy.
    """
    digits = _CARD_SEPARATORS.sub("", raw)
    if not digits.isdigit() or not 12 <= len(digits) <= 19:
        return False
    if card_brand(digits) is None:
        return False
    return luhn_ok(digits)


# ---------------------------------------------------------------------------
# Aadhaar — Verhoeff, plus the first-digit rule
# ---------------------------------------------------------------------------

_VERHOEFF_D: tuple[tuple[int, ...], ...] = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

_VERHOEFF_P: tuple[tuple[int, ...], ...] = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)

_AADHAAR_SEPARATORS = re.compile(r"[ \-\u00a0]")


def verhoeff_ok(digits: str) -> bool:
    """Verhoeff dihedral-group checksum over a digits-only string."""
    if not digits.isdigit() or not digits:
        return False
    check = 0
    for index, char in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[index % 8][ord(char) - 48]]
    return check == 0


def validate_aadhaar(raw: str) -> bool:
    """Twelve digits, Verhoeff-valid, and a first digit UIDAI never issues.

    The first-digit rule is not cosmetic. UIDAI reserves 0 and 1 so that an
    Aadhaar can never be confused with a numbering scheme that pads, and
    without the rule a Verhoeff-valid twelve-digit run starting `0000` — an
    ordinary padded reference number — reads as an Aadhaar and gets redacted.
    """
    digits = _AADHAAR_SEPARATORS.sub("", raw)
    if len(digits) != 12 or not digits.isdigit():
        return False
    if digits[0] in ("0", "1"):
        return False
    return verhoeff_ok(digits)


# ---------------------------------------------------------------------------
# PAN (India)
# ---------------------------------------------------------------------------

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

#: The fourth character declares the holder type. Anything outside this set is
#: not a PAN, whatever the shape says.
#:
#:   A AOP   B BOI       C Company   E LLP    F Firm/Partnership
#:   G Government        H HUF       J Artificial Juridical Person
#:   K Krish (Trust)     L Local Authority    P Individual   T Trust
PAN_HOLDER_TYPES: frozenset[str] = frozenset("ABCEFGHJKLPT")


def validate_pan_india(raw: str) -> bool:
    """Shape plus the holder-type character. PAN carries no checksum."""
    candidate = raw.strip().upper().replace(" ", "")
    if not _PAN_RE.match(candidate):
        return False
    return candidate[3] in PAN_HOLDER_TYPES


# ---------------------------------------------------------------------------
# GSTIN
# ---------------------------------------------------------------------------

_GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

#: State codes actually allotted. 01-38 plus 97 (Other Territory) and 99
#: (Centre Jurisdiction). 00 is the gap that a zero-padded internal reference
#: number falls into, which is the reason to check at all.
_GSTIN_STATE_CODES: frozenset[str] = frozenset(
    [f"{n:02d}" for n in range(1, 39)] + ["97", "99"]
)


def gstin_check_character(first_fourteen: str) -> Optional[str]:
    """The 15th character GSTN computes for the first fourteen.

    Base-36 positional weighting, factors alternating 1 and 2 from the left,
    each product folded by `quotient + remainder` before summing. Returns None
    if the input is not fourteen characters of the GSTIN alphabet.
    """
    if len(first_fourteen) != 14:
        return None
    total = 0
    for index, char in enumerate(first_fourteen):
        position = _GSTIN_ALPHABET.find(char)
        if position < 0:
            return None
        product = position * (1 if index % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _GSTIN_ALPHABET[(36 - (total % 36)) % 36]


def validate_gstin(raw: str) -> bool:
    """Shape, an allotted state code, an embedded valid PAN, and the check char."""
    candidate = raw.strip().upper().replace(" ", "")
    if len(candidate) != 15 or not _GSTIN_RE.match(candidate):
        return False
    if candidate[:2] not in _GSTIN_STATE_CODES:
        return False
    # Positions 2..11 are the holder's PAN. Checking it here rather than
    # trusting the check character catches a transcription that happens to
    # re-balance the base-36 sum, which is rarer than a typo but not rare
    # enough to redact on.
    if not validate_pan_india(candidate[2:12]):
        return False
    return gstin_check_character(candidate[:14]) == candidate[14]


# ---------------------------------------------------------------------------
# US Social Security Number
# ---------------------------------------------------------------------------

_SSN_RE = re.compile(r"^(\d{3})-?(\d{2})-?(\d{4})$")


def validate_us_ssn(raw: str) -> bool:
    """SSA's structural rules. There is no checksum; these are all there is.

    Area 000, 666 and 900-999 are never issued (900-999 is the ITIN range),
    and neither group 00 nor serial 0000 exists. Together they reject the
    placeholder values that appear on blank forms — `000-00-0000` and
    `999-99-9999` — which would otherwise be redacted on every template in a
    tenant's corpus.
    """
    candidate = raw.strip().replace(" ", "").replace("\u2013", "-")
    match = _SSN_RE.match(candidate)
    if not match:
        return False
    area, group, serial = match.groups()
    if area == "000" or area == "666" or area[0] == "9":
        return False
    if group == "00" or serial == "0000":
        return False
    return True


# ---------------------------------------------------------------------------
# IBAN
# ---------------------------------------------------------------------------

_IBAN_SEPARATORS = re.compile(r"[ \-\u00a0]")

#: ISO 13616 registry lengths. An IBAN of the right shape and the wrong length
#: for its country is a typo, and mod-97 alone will not catch every one.
IBAN_LENGTHS: dict[str, int] = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16,
    "BG": 22, "BH": 22, "BI": 27, "BR": 29, "BY": 28, "CH": 21, "CR": 22,
    "CY": 28, "CZ": 24, "DE": 22, "DJ": 27, "DK": 18, "DO": 28, "EE": 20,
    "EG": 29, "ES": 24, "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22,
    "GI": 23, "GL": 18, "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22,
    "IL": 23, "IQ": 23, "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20,
    "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "LY": 25,
    "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30,
    "NL": 18, "NO": 15, "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29,
    "RO": 24, "RS": 22, "RU": 33, "SA": 24, "SC": 31, "SD": 18, "SE": 24,
    "SI": 19, "SK": 24, "SM": 27, "SO": 23, "ST": 25, "SV": 28, "TL": 23,
    "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}


def iban_mod97(candidate: str) -> Optional[int]:
    """ISO 7064 mod-97-10 over a whitespace-stripped IBAN. 1 means valid.

    Computed by streaming remainders rather than building the full integer:
    a 34-character IBAN becomes a 40-digit number, and `int()` on untrusted
    length is the kind of thing that is fine until someone pastes a page.
    """
    text = candidate.strip().upper()
    if len(text) < 5 or not text.isalnum():
        return None
    rotated = text[4:] + text[:4]
    remainder = 0
    for char in rotated:
        if char.isdigit():
            remainder = (remainder * 10 + (ord(char) - 48)) % 97
        elif "A" <= char <= "Z":
            remainder = (remainder * 100 + (ord(char) - 55)) % 97
        else:
            return None
    return remainder


def validate_iban(raw: str) -> bool:
    """Registry country, registry length, and ISO 7064 mod-97 equal to 1."""
    candidate = _IBAN_SEPARATORS.sub("", raw).strip().upper()
    if len(candidate) < 15 or not candidate.isalnum():
        return False
    if not candidate[:2].isalpha() or not candidate[2:4].isdigit():
        return False
    expected = IBAN_LENGTHS.get(candidate[:2])
    if expected is None or len(candidate) != expected:
        return False
    return iban_mod97(candidate) == 1


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

VALIDATORS: dict[str, Callable[[str], bool]] = {
    "card_number": validate_card_number,
    "aadhaar": validate_aadhaar,
    "pan_india": validate_pan_india,
    "gstin": validate_gstin,
    "us_ssn": validate_us_ssn,
    "iban": validate_iban,
}


def validator_for(detector: str) -> Optional[Callable[[str], bool]]:
    """The validator for a detector, or None for pattern-only detectors.

    None is a meaningful answer: `email` and `phone` have no arithmetic to
    check, and their confidence comes from context words instead. Callers must
    not treat a missing validator as "accept" without also lowering confidence
    — see `detect.py`, which does exactly that.
    """
    return VALIDATORS.get(detector)