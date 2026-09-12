"""ARCH-31 Step 0 — normalization, and the home ARCH-30 A2 was waiting for.

WHY THIS MODULE IS WHERE A2 LANDS
=================================

The D-5 audit found `workspaces.date_format` and `workspaces.currency` stored,
editable in Workspace Settings, and read by nothing. A2 was deferred through
all of Tranche 4 for one reason: those two settings answer questions that only
arise once something tries to *interpret* a document, and until ARCH-31 nothing
did. A currency preference has no meaning until an amount arrives with no
symbol; a date format has no meaning until `03/04/2026` arrives.

Both of those happen here, and nowhere else. Three-way matching compares an
amount on a purchase order with an amount on an invoice, and if one of them was
read as 1,234.56 and the other as 1.23456, the match is wrong in a way that
looks like a price variance and will be escalated to a human as a supplier
dispute. That is the failure this module exists to prevent.

DESIGN RULES
============

*Pure.* No `Session`, no I/O, no clock. Workspace settings arrive as arguments,
never as a lookup. That is what makes the whole module gateable offline, and
`verify_arch31_step0.py` exercises every branch below without a database.

*Ambiguity is resolved explicitly or refused, never guessed.* Every function
that can face an ambiguous input takes a hint and raises `AmbiguousValue` when
it has none and the input genuinely admits two readings. A parser that silently
picks one reading of `03/04/2026` produces a plausible wrong answer, and a
plausible wrong answer in a matching engine is worse than a refusal — the
refusal gets a human; the wrong answer gets an approved invoice.

*Micros, never floats.* Every amount is an integer count of millionths. The
codebase already stores money this way (`unit_price_micros`,
`proration_micros`); introducing a float here would put rounding error into the
one comparison the product is named after.

*Deterministic.* Same input, same output, forever. The matcher hashes its
inputs into `input_digest` for reproducibility, and a normalizer that changed
its mind between runs would make every digest meaningless.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

__all__ = [
    "NormalizationError",
    "AmbiguousValue",
    "Money",
    "Quantity",
    "vendor_key",
    "document_number",
    "money_micros",
    "parse_document_date",
    "quantity",
    "duration_days",
    "sku",
    "MICROS",
]

MICROS = 1_000_000


class NormalizationError(ValueError):
    """The input could not be read as the requested kind of value."""


class AmbiguousValue(NormalizationError):
    """The input has more than one valid reading and no hint was supplied.

    Separate from `NormalizationError` because the caller's response differs.
    A malformed value is a document problem; an ambiguous value is a
    *configuration* problem, and the fix is to set the workspace's date format
    or currency rather than to re-scan the page.
    """


# ===========================================================================
# Vendor identity
# ===========================================================================

#: Legal-form suffixes stripped before comparing names. Ordered longest-first
#: at use time so "Private Limited" is removed before "Limited".
_LEGAL_SUFFIXES = (
    "private limited",
    "public limited company",
    "limited liability partnership",
    "limited liability company",
    "besloten vennootschap",
    "aktiengesellschaft",
    "incorporated",
    "corporation",
    "company",
    "limited",
    "pvt ltd",
    "pvt. ltd.",
    "p ltd",
    "llp",
    "llc",
    "ltd",
    "inc",
    "corp",
    "co",
    "plc",
    "gmbh",
    "ag",
    "bv",
    "nv",
    "sarl",
    "sas",
    "sa",
    "srl",
    "spa",
    "oy",
    "ab",
    "as",
    "aps",
    "pte ltd",
    "pte",
    "sdn bhd",
)

#: GSTIN: 2-digit state, 10-char PAN, entity digit, 'Z', checksum.
_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")
#: EU VAT: 2-letter country + 2..12 alphanumerics.
_EU_VAT = re.compile(r"^(AT|BE|BG|HR|CY|CZ|DK|EE|FI|FR|DE|EL|GR|HU|IE|IT|LV|LT|LU|MT|NL|PL|PT|RO|SK|SI|ES|SE|GB|XI)[0-9A-Z]{2,12}$")
#: US EIN: nine digits, conventionally written NN-NNNNNNN.
_EIN = re.compile(r"^[0-9]{9}$")


def _ascii_fold(value: str) -> str:
    """Strip accents without dropping non-Latin scripts entirely.

    NFKD then discarding combining marks. A vendor written "Café Foods" on one
    document and "Cafe Foods" on the next must produce one key, or every
    invoice from them opens as NOT_ORDERED.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _normalized_tax_id(raw: str) -> Optional[tuple[str, str]]:
    """Classify a tax id, returning `(scheme, canonical)` or None.

    Precedence when a document carries more than one is the caller's problem —
    `vendor_key` applies it — but the recognition is here so that "GST No.
    29AABCU9603R1ZM" and "29aabcu9603r1zm" land on the same scheme and value.
    """
    compact = re.sub(r"[^0-9A-Za-z]", "", raw or "").upper()
    if not compact:
        return None
    if _GSTIN.match(compact):
        return ("GSTIN", compact)
    if _EU_VAT.match(compact):
        return ("VAT", compact)
    if _EIN.match(compact):
        return ("EIN", compact)
    # Recognised as *something* but not one of the three schemes. Still usable
    # as an identity — two documents carrying the same unknown-scheme id are
    # still the same vendor — but ranked below the known schemes.
    return ("TAXID", compact)


#: Highest first. A GSTIN is issued per state per PAN and is the strongest
#: identity available on an Indian invoice; an EU VAT number is registry-backed;
#: an EIN is federal and stable but reused across DBAs. Anything else is a
#: string somebody typed.
_SCHEME_RANK = {"GSTIN": 0, "VAT": 1, "EIN": 2, "TAXID": 3}


def vendor_key(name: Optional[str], tax_id: Optional[str] = None) -> str:
    """A stable identity for a supplier across documents.

    Tax id wins whenever one is present and recognisable, because a name is a
    rendering choice and a tax id is a registration. "Acme Pvt Ltd", "ACME
    PRIVATE LIMITED" and "Acme" are the same supplier; so are "Acme" and
    "Acme India" when both carry 29AABCU9603R1ZM, and *only* the tax id can
    tell you that.

    Falls back to the folded, suffix-stripped name. Returns a prefixed key —
    `gstin:29AABCU9603R1ZM` or `name:acme` — so that a downstream comparison
    can never accidentally match a name against a tax id, and so that a human
    reading a `procurement_case` row can see which evidence produced it.

    Raises `NormalizationError` when neither input yields anything, rather
    than returning an empty key: an empty key would collide with every other
    empty key and silently merge unrelated suppliers.
    """
    if tax_id:
        classified = _normalized_tax_id(tax_id)
        if classified is not None:
            scheme, canonical = classified
            return f"{scheme.lower()}:{canonical}"

    folded = _ascii_fold(name or "").casefold()
    # Collapse punctuation to spaces first so "acme, inc." and "acme inc"
    # reduce identically before suffix stripping.
    folded = re.sub(r"[^\w\s]", " ", folded)
    folded = re.sub(r"\s+", " ", folded).strip()

    if not folded:
        raise NormalizationError(
            "A vendor needs a name or a tax id; both were empty. An empty "
            "vendor key would collide with every other empty key and merge "
            "unrelated suppliers into one."
        )

    # A name that is NOTHING but a legal form identifies no supplier. Checked
    # before the loop, because the loop only strips a suffix preceded by a
    # space and would therefore hand back "ltd" as a vendor key — which then
    # matches every other document whose vendor field held only "Ltd".
    if folded in _LEGAL_SUFFIXES:
        raise NormalizationError(
            f"Vendor name {name!r} is a legal form with no company name "
            "attached; it cannot identify a supplier."
        )

    # Longest suffix first, and repeatedly: "Acme Pvt Ltd Co" sheds both.
    changed = True
    while changed:
        changed = False
        for suffix in sorted(_LEGAL_SUFFIXES, key=len, reverse=True):
            if folded.endswith(" " + suffix):
                folded = folded[: -(len(suffix) + 1)].strip()
                changed = True
                break
        if folded in _LEGAL_SUFFIXES:
            raise NormalizationError(
                f"Vendor name {name!r} is nothing but legal-form suffixes "
                "once normalised; it cannot identify a supplier."
            )

    folded = re.sub(r"\s+", " ", folded).strip()
    if not folded:
        raise NormalizationError(
            f"Vendor name {name!r} is nothing but legal-form suffixes once "
            "normalised; it cannot identify a supplier."
        )
    return f"name:{folded}"


# ===========================================================================
# Document numbers and SKUs
# ===========================================================================

#: Generic labels only. Short supplier codes — INV, PO, GRN, CN — are
#: deliberately NOT here: "INV-0004512" is a number whose scheme includes the
#: prefix, and stripping it would make INV-4512 and PO-4512 collide. The words
#: below are labels a human typed in front of a number, never part of it.
_DOCNUM_PREFIXES = re.compile(
    r"^(?:invoice|bill|purchase\s*order|goods\s*receipt|receipt|"
    r"credit\s*note|no\.?|number|num|ref(?:erence)?|#)\s*[:\-#/]?\s*",
    re.IGNORECASE,
)


def document_number(raw: Optional[str]) -> str:
    """Canonical form of an invoice / PO / receipt number.

    Upper-cased, whitespace and `#` removed, leading labels like "Invoice No."
    dropped, and leading zeros stripped from each numeric run.

    Leading zeros are the one that matters. A supplier's ERP prints
    `INV-0004512`; their statement prints `INV-4512`; a human typed `inv 4512`
    into a PO. All three are one document, and a matcher that treats them as
    three opens two spurious NOT_ORDERED cases per invoice.

    Short supplier codes survive. `INV-0004512` normalises to `INV-4512` and
    `inv 4512` normalises to the same thing, because the space becomes the
    same hyphen; but the generic labels ("Invoice", "No.", "#", "Ref") are
    stripped, so `Invoice No. 4512` becomes `4512`. The rule is that a word
    describing the document is a label and a code inside the identifier is
    part of it — stripping `INV` as well would make `INV-4512` and `PO-4512`
    collide, which is the one outcome a matcher must never produce.

    Separators are preserved (as `-`) rather than removed, because
    `INV-2026-0001` and `INV-20260001` are genuinely different numbering
    schemes at some suppliers and collapsing them creates false matches. The
    normalisation is aggressive about noise and conservative about structure.
    """
    text = (raw or "").strip()
    if not text:
        raise NormalizationError("A document number cannot be empty.")

    text = _ascii_fold(text)
    previous = None
    while previous != text:
        previous = text
        text = _DOCNUM_PREFIXES.sub("", text).strip()

    text = text.upper()
    text = text.replace("#", "")
    # Any run of separators — including whitespace — becomes a single hyphen,
    # so "inv 4512" and "INV-4512" converge. Deleting whitespace instead
    # would produce "INV4512" from one document and "INV-4512" from the other
    # and open a spurious NOT_ORDERED case for every such pair.
    text = re.sub(r"[^\w]+", "-", text)
    text = text.strip("-")
    # Strip leading zeros inside each numeric run, keeping a bare "0".
    text = re.sub(r"(?<![0-9])0+(?=[0-9])", "", text)

    if not text:
        raise NormalizationError(
            f"Document number {raw!r} normalises to nothing; it is a label "
            "with no identifier attached."
        )
    return text


def sku(raw: Optional[str]) -> str:
    """Canonical SKU: upper-cased alphanumerics, every separator removed.

    Unlike `document_number`, separators ARE removed here. SKUs are written
    `ABC-123`, `ABC 123` and `abc123` interchangeably by the same supplier on
    the same day, and there is no numbering scheme to preserve — a SKU is an
    opaque identifier, so the only safe normalisation is the most aggressive
    one. This asymmetry with `document_number` is deliberate and is gated.
    """
    text = _ascii_fold((raw or "").strip()).upper()
    text = re.sub(r"[^0-9A-Z]+", "", text)
    if not text:
        raise NormalizationError(f"SKU {raw!r} contains no alphanumerics.")
    return text


# ===========================================================================
# Money
# ===========================================================================


@dataclass(frozen=True)
class Money:
    """An amount in integer millionths, with the currency that was resolved."""

    micros: int
    currency: str
    #: True when the currency came from the workspace setting rather than
    #: from the document. Surfaced in evidence so a reviewer can see that the
    #: page did not say "INR" — the workspace did.
    currency_from_workspace: bool = False


_CURRENCY_SYMBOLS = {
    "₹": "INR",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₩": "KRW",
}

_CURRENCY_WORDS = {
    "RS": "INR",
    "RS.": "INR",
    "INR": "INR",
    "USD": "USD",
    "US$": "USD",
    "EUR": "EUR",
    "GBP": "GBP",
    "JPY": "JPY",
    "AUD": "AUD",
    "CAD": "CAD",
    "SGD": "SGD",
    "AED": "AED",
}

#: Indian grouping: the last group is three digits, every earlier group is two.
_INDIAN_GROUPING = re.compile(r"^\d{1,2}(?:,\d{2})+,\d{3}$")
#: Western grouping: every group after the first is exactly three.
_WESTERN_GROUPING = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")

#: Currencies whose conventional grouping puts the decimal comma last.
_COMMA_DECIMAL_CURRENCIES = {"EUR", "BRL", "DKK", "SEK", "NOK", "PLN", "CZK", "TRY"}


def _detect_currency(raw: str) -> tuple[Optional[str], str]:
    """Pull a currency out of the text, returning `(code, remaining_text)`."""
    text = raw
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code, text.replace(symbol, " ")
    upper = text.upper()
    for word, code in sorted(_CURRENCY_WORDS.items(), key=lambda kv: -len(kv[0])):
        # Word boundary on the left only: "Rs.1,234" has no space.
        match = re.search(rf"(?<![A-Z]){re.escape(word)}", upper)
        if match:
            start, end = match.span()
            return code, text[:start] + " " + text[end:]
    return None, text


def money_micros(
    raw: Optional[str],
    *,
    currency_hint: Optional[str] = None,
    workspace_currency: Optional[str] = None,
) -> Money:
    """Parse an amount in any of the groupings this product will actually meet.

    Handles, and is gated on:

        1,23,456.78    Indian grouping, decimal point      -> 123456.78
        1.234.567,89   EU grouping, decimal comma          -> 1234567.89
        1,234,567.89   Western grouping, decimal point     -> 1234567.89
        1234.56        no grouping
        (1,234.56)     accounting negative
        1,234.56-      trailing-sign negative (SAP exports)
        ₹ 1,23,456     symbol, no decimals
        Rs. 1,23,456   currency word

    THE AMBIGUOUS CASE, AND WHY IT IS REFUSED
    -----------------------------------------
    `1.234` is 1234 in Germany and 1.234 in India. `1,234` is 1234 in India and
    1.234 in Germany. A single separator followed by exactly three digits is
    genuinely undecidable from the string alone.

    Resolution order: the currency found on the document, then `currency_hint`,
    then `workspace_currency` — which is ARCH-30 A2, the first and only reader
    those settings have ever had. If none of the three resolves it, this raises
    `AmbiguousValue` rather than picking. A silent wrong reading here is a
    1000x error on a line item, and it will surface downstream as a price
    variance that a human is asked to approve.

    `workspace_currency` never overrides a currency printed on the page. The
    document is evidence; the setting is a default.
    """
    text = (raw or "").strip()
    if not text:
        raise NormalizationError("An amount cannot be empty.")

    detected, text = _detect_currency(text)
    currency_from_workspace = False
    if detected is not None:
        currency = detected
    elif currency_hint:
        currency = currency_hint.upper()
    elif workspace_currency:
        currency = workspace_currency.upper()
        currency_from_workspace = True
    else:
        currency = None

    negative = False
    stripped = text.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        negative = True
        stripped = stripped[1:-1]
    stripped = stripped.strip()
    if stripped.endswith("-"):
        negative = True
        stripped = stripped[:-1].strip()
    if stripped.startswith("-"):
        negative = True
        stripped = stripped[1:].strip()
    if stripped.startswith("+"):
        stripped = stripped[1:].strip()

    # Everything that is not a digit or a separator is noise by this point.
    stripped = re.sub(r"[^\d.,]", "", stripped)
    if not stripped or not re.search(r"\d", stripped):
        raise NormalizationError(f"Amount {raw!r} contains no digits.")

    dots = stripped.count(".")
    commas = stripped.count(",")

    if dots and commas:
        # Whichever appears last is the decimal separator; the other groups.
        decimal_sep = "." if stripped.rindex(".") > stripped.rindex(",") else ","
        group_sep = "," if decimal_sep == "." else "."
        integer_part, _, fraction = stripped.rpartition(decimal_sep)
        integer_part = integer_part.replace(group_sep, "")
    elif dots or commas:
        sep = "." if dots else ","
        count = dots or commas
        head, _, tail = stripped.rpartition(sep)
        if count > 1:
            # Repeated separator can only be grouping.
            integer_part, fraction = stripped.replace(sep, ""), ""
        elif len(tail) != 3:
            # Not a group of three, so it is a decimal fraction.
            integer_part, fraction = head, tail
        else:
            # Exactly three trailing digits: genuinely ambiguous.
            resolved = _resolve_single_separator(sep, currency)
            if resolved is None:
                raise AmbiguousValue(
                    f"{raw!r} could be {head}{sep}{tail} read as a decimal or "
                    f"as {head}{tail} read with grouping, and no currency was "
                    f"found on the document, supplied as a hint, or set on the "
                    f"workspace. Set the workspace currency in Workspace "
                    f"Settings so amounts like this resolve."
                )
            if resolved == "grouping":
                integer_part, fraction = stripped.replace(sep, ""), ""
            else:
                integer_part, fraction = head, tail
    else:
        integer_part, fraction = stripped, ""

    integer_part = integer_part or "0"
    if not integer_part.isdigit():
        raise NormalizationError(f"Amount {raw!r} is not a number.")
    if fraction and not fraction.isdigit():
        raise NormalizationError(f"Amount {raw!r} has a malformed fraction.")

    try:
        value = Decimal(f"{integer_part}.{fraction or '0'}")
    except InvalidOperation as exc:  # pragma: no cover - guarded above
        raise NormalizationError(f"Amount {raw!r} is not a number.") from exc

    micros = int((value * MICROS).to_integral_value())
    if negative:
        micros = -micros

    return Money(
        micros=micros,
        currency=(currency or "XXX"),
        currency_from_workspace=currency_from_workspace,
    )


def _resolve_single_separator(sep: str, currency: Optional[str]) -> Optional[str]:
    """Decide whether a lone separator before three digits groups or divides.

    Returns "grouping", "decimal", or None when it cannot be decided.
    """
    if currency is None:
        return None
    comma_decimal = currency in _COMMA_DECIMAL_CURRENCIES
    if sep == ",":
        # In a comma-decimal locale a comma before three digits is a decimal
        # with three places (rare but legal); elsewhere it groups thousands.
        return "decimal" if comma_decimal else "grouping"
    # sep == "."
    return "grouping" if comma_decimal else "decimal"


# ===========================================================================
# Dates
# ===========================================================================

#: Workspace `date_format` values, mapped to which component leads.
_DAY_FIRST_FORMATS = {"DD/MM/YYYY", "DD-MM-YYYY", "DD.MM.YYYY", "D/M/YYYY"}
_MONTH_FIRST_FORMATS = {"MM/DD/YYYY", "MM-DD-YYYY", "M/D/YYYY"}
_ISO_FORMATS = {"YYYY-MM-DD", "YYYY/MM/DD"}

_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_NUMERIC_DATE = re.compile(r"^(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})$")
_TEXT_DATE = re.compile(
    r"^(\d{1,2})\s*(?:st|nd|rd|th)?\s+([A-Za-z]+)\.?,?\s+(\d{2,4})$"
)
_TEXT_DATE_MONTH_FIRST = re.compile(
    r"^([A-Za-z]+)\.?\s+(\d{1,2})\s*(?:st|nd|rd|th)?,?\s+(\d{2,4})$"
)


def _expand_year(value: int) -> int:
    """Two-digit years. 70..99 -> 19xx, 00..69 -> 20xx.

    A purchase order dated '69 is not a purchase order this system will ever
    see; one dated '99 plausibly is, in a migrated archive.
    """
    if value >= 100:
        return value
    return 1900 + value if value >= 70 else 2000 + value


def parse_document_date(
    raw: Optional[str], *, format_hint: Optional[str] = None
) -> date:
    """Read a date off a document, resolving `03/04/2026` with the workspace.

    THE AMBIGUITY THIS EXISTS FOR
    -----------------------------
    `03/04/2026` is 3 April in India and the UK, and 4 March in the United
    States. Both readings are valid dates, so nothing in the string tells you
    which. On a three-way match this decides whether a goods receipt precedes
    its invoice, which decides whether the case is clean or an exception.

    `format_hint` is `workspaces.date_format` — ARCH-30 A2's second reader.
    When the two leading components make only one reading possible (a value
    above 12 in one position, or a four-digit year first) the hint is not
    consulted at all: evidence beats configuration.

    When it IS ambiguous and no hint was supplied, this raises `AmbiguousValue`
    rather than defaulting to either convention. Defaulting would be wrong for
    roughly half of all customers and silent for all of them.

    Text months ("3 April 2026", "April 3, 2026") are never ambiguous and are
    accepted without a hint.
    """
    text = (raw or "").strip()
    if not text:
        raise NormalizationError("A document date cannot be empty.")

    text = _ascii_fold(text)
    text = re.sub(r"\s+", " ", text).strip()

    match = _TEXT_DATE.match(text)
    if match:
        day, month_name, year = match.groups()
        month = _MONTH_NAMES.get(month_name.lower())
        if month is None:
            raise NormalizationError(f"{month_name!r} is not a month name.")
        return _build_date(_expand_year(int(year)), month, int(day), raw)

    match = _TEXT_DATE_MONTH_FIRST.match(text)
    if match:
        month_name, day, year = match.groups()
        month = _MONTH_NAMES.get(month_name.lower())
        if month is None:
            raise NormalizationError(f"{month_name!r} is not a month name.")
        return _build_date(_expand_year(int(year)), month, int(day), raw)

    match = _NUMERIC_DATE.match(text)
    if not match:
        raise NormalizationError(f"{raw!r} is not a date this parser reads.")

    first, second, third = (int(part) for part in match.groups())
    first_raw = match.group(1)

    # ISO: a four-digit leading year is unambiguous whatever the hint says.
    if len(first_raw) == 4:
        return _build_date(first, second, third, raw)

    day_first: Optional[bool] = None
    if first > 12 and second <= 12:
        day_first = True
    elif second > 12 and first <= 12:
        day_first = False

    if day_first is None:
        hint = (format_hint or "").strip().upper()
        if hint in _DAY_FIRST_FORMATS:
            day_first = True
        elif hint in _MONTH_FIRST_FORMATS:
            day_first = False
        elif hint in _ISO_FORMATS:
            # An ISO workspace meeting a two-digit-leading date tells us
            # nothing about which of the two leading components is the day.
            day_first = None
        if day_first is None:
            raise AmbiguousValue(
                f"{raw!r} reads as both a day-first and a month-first date, "
                f"and the workspace date format "
                f"{format_hint or 'is not set'!r} does not resolve it. Set "
                f"the workspace date format in Workspace Settings."
            )

    year = _expand_year(third)
    if day_first:
        return _build_date(year, second, first, raw)
    return _build_date(year, first, second, raw)


def _build_date(year: int, month: int, day: int, raw: Optional[str]) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise NormalizationError(
            f"{raw!r} resolves to {year:04d}-{month:02d}-{day:02d}, which is "
            f"not a real date."
        ) from exc


# ===========================================================================
# Quantities
# ===========================================================================


@dataclass(frozen=True)
class Quantity:
    """A count, its unit, and the pack size it was expressed with."""

    value: Decimal
    unit: Optional[str] = None
    #: For "2 boxes of 10": `value` is 2, `pack_size` is 10, `total` is 20.
    pack_size: Optional[Decimal] = None

    @property
    def total(self) -> Decimal:
        return self.value * (self.pack_size if self.pack_size is not None else 1)


_UNIT_ALIASES = {
    "pc": "PCS", "pcs": "PCS", "piece": "PCS", "pieces": "PCS",
    "ea": "EA", "each": "EA", "unit": "EA", "units": "EA", "nos": "EA",
    "no": "EA", "qty": "EA",
    "kg": "KG", "kgs": "KG", "kilogram": "KG", "kilograms": "KG",
    "g": "G", "gm": "G", "gms": "G", "gram": "G", "grams": "G",
    "mt": "MT", "ton": "MT", "tons": "MT", "tonne": "MT", "tonnes": "MT",
    "l": "L", "ltr": "L", "litre": "L", "litres": "L", "liter": "L",
    "ml": "ML",
    "m": "M", "mtr": "M", "meter": "M", "metre": "M", "meters": "M",
    "cm": "CM", "mm": "MM",
    "box": "BOX", "boxes": "BOX", "ctn": "CTN", "carton": "CTN",
    "cartons": "CTN", "case": "CASE", "cases": "CASE",
    "pkt": "PKT", "packet": "PKT", "packets": "PKT", "pack": "PKT",
    "packs": "PKT", "bag": "BAG", "bags": "BAG",
    "hr": "HR", "hrs": "HR", "hour": "HR", "hours": "HR",
    "day": "DAY", "days": "DAY", "month": "MONTH", "months": "MONTH",
}

_PACK_OF = re.compile(
    r"^([\d.,]+)\s*([A-Za-z]+)?\s+of\s+([\d.,]+)\s*([A-Za-z]+)?$", re.IGNORECASE
)
_QTY = re.compile(r"^([\d.,]+)\s*([A-Za-z]+)?\.?$")


def _decimal_from(raw: str) -> Decimal:
    """Quantities use the same grouping rules as money, minus the currency.

    Re-uses `money_micros` deliberately rather than a second parser: a
    quantity written `1,234.500` must not be read one way on a PO and another
    on the invoice because two different functions read it.
    """
    parsed = money_micros(raw, currency_hint="USD")
    return Decimal(parsed.micros) / MICROS


def quantity(raw: Optional[str]) -> Quantity:
    """Parse `12 pcs`, `3.5 kg`, `2 boxes of 10`, `1,200`, `10`.

    `2 boxes of 10` keeps the shape rather than flattening to 20. The matcher
    compares `total`, but the case detail shows the reviewer what the document
    actually said, and "2 boxes of 10" reconciled against "20 pcs" is a
    comprehensible line where a bare "20 vs 20" hides the conversion that made
    it work.
    """
    text = (raw or "").strip()
    if not text:
        raise NormalizationError("A quantity cannot be empty.")
    text = re.sub(r"\s+", " ", _ascii_fold(text))

    match = _PACK_OF.match(text)
    if match:
        outer, outer_unit, inner, inner_unit = match.groups()
        unit = _UNIT_ALIASES.get((inner_unit or outer_unit or "").lower())
        if unit is None and (outer_unit or inner_unit):
            unit = (inner_unit or outer_unit or "").upper()
        return Quantity(
            value=_decimal_from(outer),
            unit=unit,
            pack_size=_decimal_from(inner),
        )

    match = _QTY.match(text)
    if not match:
        raise NormalizationError(f"{raw!r} is not a quantity this parser reads.")

    number, unit_text = match.groups()
    unit: Optional[str] = None
    if unit_text:
        unit = _UNIT_ALIASES.get(unit_text.lower(), unit_text.upper())
    return Quantity(value=_decimal_from(number), unit=unit)


# ===========================================================================
# Durations
# ===========================================================================

_NET_TERMS = re.compile(r"\bnet\s*[-/]?\s*(\d{1,3})\b", re.IGNORECASE)
_WITHIN_DAYS = re.compile(
    r"\bwithin\s+(\d{1,3})\s*(day|days|week|weeks|month|months)\b", re.IGNORECASE
)
_BARE_DAYS = re.compile(
    r"\b(\d{1,3})\s*(day|days|week|weeks|month|months)\b", re.IGNORECASE
)
_DUE_ON_RECEIPT = re.compile(
    r"\b(due\s+on\s+receipt|immediate|immediately|cod|cash\s+on\s+delivery)\b",
    re.IGNORECASE,
)

_UNIT_DAYS = {"day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30}


def duration_days(raw: Optional[str]) -> Optional[int]:
    """Payment terms as a whole number of days, or None when there are none.

    `Net 30`, `NET-45`, `within 45 days`, `2 weeks`, `Due on receipt` (0).
    Months are 30 days, which is what "Net 2 months" means commercially and
    is not an approximation anybody is being misled by.

    Returns None rather than raising for text that carries no term at all —
    most invoice footers do not, and a missing payment term is not a document
    defect.
    """
    text = (raw or "").strip()
    if not text:
        return None
    text = _ascii_fold(text)

    if _DUE_ON_RECEIPT.search(text):
        return 0

    match = _NET_TERMS.search(text)
    if match:
        return int(match.group(1))

    match = _WITHIN_DAYS.search(text)
    if match:
        return int(match.group(1)) * _UNIT_DAYS[match.group(2).lower()]

    match = _BARE_DAYS.search(text)
    if match:
        return int(match.group(1)) * _UNIT_DAYS[match.group(2).lower()]

    return None