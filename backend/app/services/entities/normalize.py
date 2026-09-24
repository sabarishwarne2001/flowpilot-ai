"""ARCH42-S1:normalize — names and identifiers into comparable form. Pure.

Every identifier is VALIDATED before it is stored or looked up: a PAN has its
shape, a GSTIN and an IBAN their check characters, an Aadhaar its Verhoeff
digit, a container number its ISO 6346 digit. An LLM that misreads one
character of a GSTIN produces a value that fails its checksum, and a value
that fails its checksum must never become a hard link between two records.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Optional

from app.services.entities import vocabulary as v

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_SPACE = re.compile(r"\s+")
_ALNUM_UPPER = re.compile(r"[^0-9A-Z]")

HONORIFICS = frozenset({
    "mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "madam", "shri", "sri", "smt", "kumari",
    "km", "late", "capt", "col", "adv", "er", "ca", "md", "phd", "jr", "sr",
})
ORG_SUFFIXES = frozenset({
    "ltd", "limited", "pvt", "private", "llc", "inc", "incorporated", "corp", "corporation",
    "co", "company", "llp", "plc", "gmbh", "ag", "sa", "bv", "nv", "pte", "pty", "srl", "spa",
    "oy", "ab", "kk", "sarl", "lp",
})
ORG_MARKERS = ORG_SUFFIXES | frozenset({
    "bank", "trust", "industries", "enterprises", "solutions", "services", "technologies",
    "technology", "group", "holdings", "foundation", "university", "hospital", "hospitals",
    "clinic", "logistics", "shipping", "lines", "traders", "trading", "associates", "partners",
    "systems", "labs", "laboratories", "agency", "international", "exports", "imports",
    "manufacturing", "motors", "pharma", "pharmaceuticals", "insurance", "capital", "ventures",
    "consulting", "consultants", "stores", "mart", "and", "&", "department", "ministry",
    "council", "authority", "society", "institute", "school", "college", "llp",
})
ADDRESS_ABBREVIATIONS = {
    "street": "st", "road": "rd", "avenue": "ave", "lane": "ln", "boulevard": "blvd",
    "floor": "fl", "building": "bldg", "apartment": "apt", "suite": "ste", "sector": "sec",
    "near": "nr", "opposite": "opp", "cross": "x", "main": "mn", "nagar": "ngr",
    "north": "n", "south": "s", "east": "e", "west": "w",
}


def fold(text: str) -> str:
    """NFKD, strip combining marks, casefold. 'Müller' and 'Muller' agree."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _tokens(text: str) -> list[str]:
    return [t for t in _NON_ALNUM.split(fold(text).replace("&", " and ")) if t]


def normalize_person(raw: str) -> str:
    text = raw or ""
    if text.count(",") == 1:  # "Dr. Kumar, Ravi" -> "ravi kumar"
        last, first = ([t for t in _tokens(part) if t not in HONORIFICS] for part in text.split(","))
        if first and len(last) == 1:
            return " ".join(first + last)[: v.MAX_NAME_LENGTH]
    tokens = [t for t in _tokens(text) if t not in HONORIFICS]
    return " ".join(tokens)[: v.MAX_NAME_LENGTH]


def normalize_organization(raw: str) -> str:
    tokens = _tokens(raw)
    if tokens and tokens[0] == "the":
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1] in ORG_SUFFIXES:
        tokens = tokens[:-1]
    return " ".join(tokens)[: v.MAX_NAME_LENGTH]


def normalize_address(raw: str) -> str:
    tokens = [ADDRESS_ABBREVIATIONS.get(t, t) for t in _tokens(raw)]
    return " ".join(tokens)[: v.MAX_NAME_LENGTH]


def party_kind(raw: str) -> str:
    """PARTY -> PERSON or ORGANIZATION, by whether the name reads like a company."""
    tokens = set(_tokens(raw))
    if "&" in (raw or ""):
        return v.KIND_ORGANIZATION
    return v.KIND_ORGANIZATION if tokens & ORG_MARKERS else v.KIND_PERSON


def normalize_name(kind: str, raw: str) -> str:
    if kind == v.KIND_PERSON:
        return normalize_person(raw)
    if kind == v.KIND_ORGANIZATION:
        return normalize_organization(raw)
    if kind == v.KIND_ADDRESS:
        return normalize_address(raw)
    return _SPACE.sub(" ", fold(raw)).strip()[: v.MAX_NAME_LENGTH]


def clean_display(raw: str) -> str:
    return _SPACE.sub(" ", (raw or "").strip())[: v.MAX_NAME_LENGTH]


# ---------------------------------------------------------------------------
# String similarity (pure; the gates compare it to hand-computed values)
# ---------------------------------------------------------------------------


def jaro_winkler(a: str, b: str, prefix_scale: float = 0.1) -> float:
    if a == b:
        return 1.0 if a else 0.0
    if not a or not b:
        return 0.0
    window = max(0, max(len(a), len(b)) // 2 - 1)
    a_flags = [False] * len(a)
    b_flags = [False] * len(b)
    matches = 0
    for i, ch in enumerate(a):
        lo, hi = max(0, i - window), min(len(b), i + window + 1)
        for j in range(lo, hi):
            if not b_flags[j] and b[j] == ch:
                a_flags[i] = b_flags[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    a_seq = [a[i] for i in range(len(a)) if a_flags[i]]
    b_seq = [b[j] for j in range(len(b)) if b_flags[j]]
    transpositions = sum(1 for x, y in zip(a_seq, b_seq) if x != y) / 2
    jaro = (matches / len(a) + matches / len(b) + (matches - transpositions) / matches) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * prefix_scale * (1 - jaro)


def token_set_equal(a: str, b: str) -> bool:
    return bool(a) and sorted(a.split()) == sorted(b.split())


def initials_compatible(a: str, b: str) -> bool:
    """'r kumar' vs 'ravi kumar': same surname, first initial agrees."""
    ta, tb = a.split(), b.split()
    if len(ta) < 2 or len(tb) < 2 or ta[-1] != tb[-1]:
        return False
    return ta[0][0] == tb[0][0] and (len(ta[0]) == 1 or len(tb[0]) == 1)


# ---------------------------------------------------------------------------
# Identifiers: normalise + validate. None means "not a valid value".
# ---------------------------------------------------------------------------

_EMAIL = re.compile(r"^[a-z0-9._%+'\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")
_PAN = re.compile(r"^[A-Z]{3}[ABCFGHLJPTKE][A-Z][0-9]{4}[A-Z]$")
_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5), (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7), (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3), (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4), (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7), (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def verhoeff_valid(digits: str) -> bool:
    check = 0
    for i, ch in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[i % 8][int(ch)]]
    return check == 0


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        product = _B36.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _B36[(36 - total % 36) % 36]


def iban_valid(value: str) -> bool:
    if not 15 <= len(value) <= 34 or not value[:2].isalpha() or not value[2:4].isdigit():
        return False
    rearranged = value[4:] + value[:4]
    number = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(number) % 97 == 1


def _iso6346_value(ch: str) -> int:
    if ch.isdigit():
        return int(ch)
    value = 10
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if value % 11 == 0:
            value += 1
        if letter == ch:
            return value
        value += 1
    raise ValueError(ch)


def container_valid(value: str) -> bool:
    if not re.fullmatch(r"[A-Z]{3}[UJZ][0-9]{7}", value):
        return False
    total = sum(_iso6346_value(ch) * (2 ** i) for i, ch in enumerate(value[:10]))
    return total % 11 % 10 == int(value[10])


_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def parse_date(raw: str) -> Optional[str]:
    """ISO date or None. Numeric dates are read day-first (India, UK, EU)."""
    text = fold(raw).strip()
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", text)
        if m:
            a, b, y = int(m[1]), int(m[2]), int(m[3])
            d, mo = (a, b) if a > 12 or b <= 12 else (b, a)
        else:
            m = re.fullmatch(r"(\d{1,2})\s*([a-z]{3})[a-z]*\.?,?\s*(\d{4})", text) or None
            n = re.fullmatch(r"([a-z]{3})[a-z]*\.?\s*(\d{1,2}),?\s*(\d{4})", text) if m is None else None
            if m is not None and m[2] in _MONTHS:
                d, mo, y = int(m[1]), _MONTHS[m[2]], int(m[3])
            elif n is not None and n[1] in _MONTHS:
                d, mo, y = int(n[2]), _MONTHS[n[1]], int(n[3])
            else:
                return None
    try:
        return date(y, mo, d).isoformat() if 1900 <= y <= 2100 else None
    except ValueError:
        return None


def normalize_identifier(kind: str, raw: object) -> Optional[str]:
    """The canonical value, or None when `raw` is not a valid value of `kind`."""
    if raw is None or isinstance(raw, (dict, list, bool)):
        return None
    text = str(raw).strip()
    if not text:
        return None
    upper = _ALNUM_UPPER.sub("", text.upper())
    if kind == v.ID_EMAIL:
        value = text.lower().removeprefix("mailto:").strip()
        return value if _EMAIL.match(value) and ".." not in value else None
    if kind == v.ID_PHONE:
        digits = re.sub(r"\D", "", text)
        if digits.startswith("00"):
            digits = digits[2:]
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        return digits if 8 <= len(digits) <= 15 else None
    if kind == v.ID_PAN:
        return upper if _PAN.match(upper) else None
    if kind == v.ID_GSTIN:
        return upper if _GSTIN.match(upper) and gstin_check_char(upper[:14]) == upper[14] else None
    if kind == v.ID_IBAN:
        return upper if iban_valid(upper) else None
    if kind == v.ID_AADHAAR:
        digits = re.sub(r"\D", "", text)
        return digits if len(digits) == 12 and digits[0] in "23456789" and verhoeff_valid(digits) else None
    if kind == v.ID_PASSPORT:
        return upper if re.fullmatch(r"[A-Z0-9]{6,9}", upper) and re.search(r"\d", upper) else None
    if kind == v.ID_DATE_OF_BIRTH:
        return parse_date(text)
    if kind in (v.ID_MEDICAL_RECORD, v.ID_ACCOUNT_NUMBER):
        return upper if 4 <= len(upper) <= 34 and re.search(r"\d", upper) else None
    if kind == v.ID_CONTAINER_NUMBER:
        return upper if container_valid(upper) else None
    if kind == v.ID_BL_NUMBER:
        return upper if 6 <= len(upper) <= 20 and re.search(r"\d", upper) else None
    if kind == v.ID_CUSTOMS_DECLARATION:
        return upper if 5 <= len(upper) <= 30 and re.search(r"\d", upper) else None
    return None


def gstin_pan(gstin: str) -> Optional[str]:
    """The PAN a GSTIN embeds (characters 3-12). A sole proprietor's GSTIN
    embeds the proprietor's own PAN, which is why PAN uniqueness is per
    entity kind, not per workspace."""
    candidate = gstin[2:12] if len(gstin) == 15 else ""
    return candidate if _PAN.match(candidate) else None


def mask(kind: str, value: str) -> str:
    """What a reader sees. Never enough to reconstruct a personal identifier."""
    if kind in v.PUBLIC_REFERENCE_KINDS:
        return value
    if kind == v.ID_EMAIL:
        local, _, domain = value.partition("@")
        return f"{local[:1]}•••@{domain}"
    if kind == v.ID_AADHAAR:
        return f"XXXX XXXX {value[-4:]}"
    if kind == v.ID_DATE_OF_BIRTH:
        return f"••/••/{value[:4]}"
    if kind == v.ID_IBAN:
        return f"{value[:2]}•• •••• {value[-4:]}"
    if kind == v.ID_GSTIN:
        return f"{value[:2]}••••••••••{value[-3:]}"
    return "•" * max(0, len(value) - 4) + value[-4:]


# ---------------------------------------------------------------------------
# Deterministic detectors (the Aadhaar/PAN card preset extracts no number)
# ---------------------------------------------------------------------------

_AADHAAR_TEXT = re.compile(r"(?<!\d)([2-9]\d{3})[ \-]?(\d{4})[ \-]?(\d{4})(?!\d)")
_PAN_TEXT = re.compile(r"(?<![A-Z0-9])([A-Z]{3}[ABCFGHLJPTKE][A-Z][0-9]{4}[A-Z])(?![A-Z0-9])")


def detect_identifiers(text: str, kinds: tuple[str, ...]) -> list[tuple[str, str]]:
    """(kind, value) pairs found in `text`, checksum-valid only, de-duplicated."""
    found: list[tuple[str, str]] = []
    upper = (text or "").upper()
    if v.ID_AADHAAR in kinds:
        for m in _AADHAAR_TEXT.finditer(text or ""):
            value = normalize_identifier(v.ID_AADHAAR, "".join(m.groups()))
            if value and (v.ID_AADHAAR, value) not in found:
                found.append((v.ID_AADHAAR, value))
    if v.ID_PAN in kinds:
        for m in _PAN_TEXT.finditer(upper):
            value = normalize_identifier(v.ID_PAN, m.group(1))
            if value and (v.ID_PAN, value) not in found:
                found.append((v.ID_PAN, value))
    return found
