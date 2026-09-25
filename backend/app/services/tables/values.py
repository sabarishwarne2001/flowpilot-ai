"""ARCH44-S1:values — typed cell values. Pure; no I/O.

Money in the formats real statements print: Indian grouping (1,23,456.78),
Western (123,456.78), European (1.234,56), currency marks (Rs., INR, ₹, $, €,
£), negatives as a leading or trailing minus, parentheses, or a Dr/Cr suffix.
Dates day-first, month-first, ISO and named-month, with the day/month order
decided per COLUMN from the values that cannot be ambiguous (a 28 in the
first position means day-first for the whole column).

Digit strings with a leading zero or ten or more digits are identifiers
(cheque numbers, UTRs, account numbers), never quantities: summing them
would invent relations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional, Sequence

from app.services.tables import vocabulary as v

_SPACE = re.compile(r"\s+")
_CURRENCY = re.compile(r"^(₹|rs\.?|inr|usd|us\$|\$|€|eur|£|gbp|aed|sgd)\s*", re.I)
_CURRENCY_TAIL = re.compile(r"\s*(₹|rs\.?|inr|usd|\$|€|eur|£|gbp|aed|sgd)$", re.I)
_DRCR = re.compile(r"\s*\b(dr|cr)\.?$", re.I)
#: Integer part: Western groups of three, Indian (lakh/crore) groups of two
#: then three, or plain digits.
_INT_WESTERN = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_INT_INDIAN = re.compile(r"^\d{1,2}(?:,\d{2})*,\d{3}$")
_INT_PLAIN = re.compile(r"^\d+$")
#: European decimal comma: dot-grouped thousands ("1.234,56"), or a comma
#: followed by one or two digits ("12,50"). "1,000" is Western grouping, never
#: one-point-zero-zero-zero.
_EUROPEAN = re.compile(r"^(\d{1,3}(?:\.\d{3})+),(\d+)$|^(\d+),(\d{1,2})$")
_PERCENT = re.compile(r"^([-+]?\d+(?:\.\d+)?)\s*%$")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_MONTHS.update({"sept": 9, "june": 6, "july": 7, "january": 1, "february": 2, "march": 3, "april": 4, "august": 8,
                "september": 9, "october": 10, "november": 11, "december": 12})
_D_NUMERIC = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})$")
_D_ISO = re.compile(r"^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$")
_D_DMONY = re.compile(r"^(\d{1,2})[\s\-/.]*([A-Za-z]{3,9})[\s\-/.,]*(\d{2}|\d{4})$")
_D_MONDY = re.compile(r"^([A-Za-z]{3,9})[\s\-.]+(\d{1,2}),?[\s\-]+(\d{4})$")

ORDER_DMY = "DMY"
ORDER_MDY = "MDY"


@dataclass(frozen=True)
class Parsed:
    kind: str
    number: Optional[Decimal] = None
    when: Optional[date] = None
    decimals: int = 0
    currency: bool = False
    grouped: bool = False
    marker: Optional[str] = None


EMPTY = Parsed(v.TYPE_EMPTY)
TEXT = Parsed(v.TYPE_TEXT)


def clean(text: Optional[str]) -> str:
    return _SPACE.sub(" ", (text or "").replace(" ", " ")).strip()


def _integer_part(raw: str) -> Optional[str]:
    if _INT_PLAIN.match(raw):
        return raw
    if _INT_WESTERN.match(raw) or _INT_INDIAN.match(raw):
        return raw.replace(",", "")
    return None


def parse_number(text: str) -> Optional[Parsed]:
    """A number, amount or percentage, or None."""
    s = clean(text)
    if not s:
        return None
    pct = _PERCENT.match(s)
    if pct:
        return Parsed(v.TYPE_PERCENT, number=Decimal(pct.group(1)), decimals=len(pct.group(1).partition(".")[2]))
    negative = False
    marker = None
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    drcr = _DRCR.search(s)
    if drcr:
        marker = drcr.group(1).upper()
        s = s[:drcr.start()].strip()
        negative = negative or marker == "DR"
    currency = False
    head = _CURRENCY.match(s)
    if head:
        currency, s = True, s[head.end():]
    tail = _CURRENCY_TAIL.search(s)
    if tail:
        currency, s = True, s[:tail.start()]
    s = s.strip()
    if s.startswith(("-", "−")):
        negative, s = True, s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    if s.endswith("-") and len(s) > 1:
        negative, s = True, s[:-1].strip()
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    head = _CURRENCY.match(s)
    if head:
        currency, s = True, s[head.end():].strip()
    if not s or not s[0].isdigit():
        return None
    european = _EUROPEAN.match(s)
    if european:
        whole_eu = european.group(1) or european.group(3)
        integer = whole_eu.replace(".", "")
        fraction = european.group(2) or european.group(4)
        grouped = "." in whole_eu
    else:
        whole, dot, fraction = s.partition(".")
        if dot and not fraction.isdigit():
            return None
        integer = _integer_part(whole)
        if integer is None:
            return None
        grouped = "," in whole
        if not dot and not grouped and (len(integer) >= 10 or (len(integer) > 1 and integer.startswith("0"))):
            return None  # an identifier, not a quantity
    try:
        number = Decimal(f"{integer}.{fraction}" if fraction else integer)
    except InvalidOperation:
        return None
    if negative:
        number = -number
    money = currency or len(fraction) == 2 or (grouped and not fraction)
    return Parsed(v.TYPE_MONEY if money else v.TYPE_NUMBER, number=number, decimals=len(fraction),
                  currency=currency, grouped=grouped, marker=marker)


def _year(raw: str) -> int:
    y = int(raw)
    if len(raw) == 2:
        y += 2000 if y < 70 else 1900
    return y


def _make(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def date_parts(text: str) -> Optional[tuple[str, int, int, int]]:
    """(shape, a, b, year) for numeric dates, or ('named', month, day, year)."""
    s = clean(text)
    m = _D_NUMERIC.match(s)
    if m:
        return "numeric", int(m.group(1)), int(m.group(2)), _year(m.group(3))
    m = _D_ISO.match(s)
    if m:
        return "iso", int(m.group(2)), int(m.group(3)), int(m.group(1))
    m = _D_DMONY.match(s)
    if m and _month(m.group(2)):
        return "named", _month(m.group(2)), int(m.group(1)), _year(m.group(3))
    m = _D_MONDY.match(s)
    if m and _month(m.group(1)):
        return "named", _month(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def _month(name: str) -> int:
    key = name.lower().rstrip(".")
    return _MONTHS.get(key) or (_MONTHS.get(key[:3], 0) if key[:3] in _MONTHS and len(key) in (3, 4) else 0)


def parse_date(text: str, order: str = ORDER_DMY) -> Optional[date]:
    parts = date_parts(text)
    if parts is None:
        return None
    shape, a, b, year = parts
    if shape == "numeric":
        day, month = (a, b) if order == ORDER_DMY else (b, a)
        return _make(year, month, day)
    return _make(year, a, b)


def infer_date_order(texts: Iterable[str], default: str = ORDER_DMY) -> str:
    """Day-first unless a value can only be month-first (or vice versa)."""
    dmy = mdy = 0
    for text in texts:
        parts = date_parts(text)
        if parts is None or parts[0] != "numeric":
            continue
        a, b = parts[1], parts[2]
        if a > 12 >= b:
            dmy += 1
        elif b > 12 >= a:
            mdy += 1
    if mdy > dmy:
        return ORDER_MDY
    if dmy > mdy:
        return ORDER_DMY
    return default


def classify(text: str, order: str = ORDER_DMY) -> Parsed:
    s = clean(text)
    if not s or s in ("-", "--", "–", "—", "nil", "NIL", "Nil"):
        return EMPTY
    when = parse_date(s, order)
    if when is not None:
        return Parsed(v.TYPE_DATE, when=when)
    number = parse_number(s)
    if number is not None:
        return number
    return TEXT


def numeric_like(text: str) -> bool:
    kind = classify(text).kind
    return kind in (v.TYPE_NUMBER, v.TYPE_MONEY, v.TYPE_PERCENT, v.TYPE_DATE)


def infer_column_type(texts: Sequence[str]) -> tuple[str, str]:
    """(value_type, date_order) for a column from its body cells."""
    values = [clean(t) for t in texts if clean(t) and classify(t).kind != v.TYPE_EMPTY]
    if not values:
        return v.TYPE_TEXT, ORDER_DMY
    order = infer_date_order(values)
    kinds = [classify(t, order).kind for t in values]
    share = {k: kinds.count(k) / len(kinds) for k in set(kinds)}
    if share.get(v.TYPE_DATE, 0) >= 0.6:
        return v.TYPE_DATE, order
    numeric = share.get(v.TYPE_MONEY, 0) + share.get(v.TYPE_NUMBER, 0)
    if share.get(v.TYPE_PERCENT, 0) >= 0.6:
        return v.TYPE_PERCENT, order
    if numeric >= 0.6:
        return (v.TYPE_MONEY if share.get(v.TYPE_MONEY, 0) >= share.get(v.TYPE_NUMBER, 0) else v.TYPE_NUMBER), order
    return v.TYPE_TEXT, order


def parse_cell(text: str, column_type: str, order: str = ORDER_DMY) -> tuple[Parsed, bool]:
    """(parsed value, conforms) of one cell read as its column's type."""
    parsed = classify(text, order)
    if parsed.kind == v.TYPE_EMPTY:
        return EMPTY, True
    if column_type == v.TYPE_TEXT:
        return TEXT, True
    if column_type == v.TYPE_DATE:
        return (parsed, True) if parsed.kind == v.TYPE_DATE else (TEXT, False)
    if column_type in (v.TYPE_MONEY, v.TYPE_NUMBER):
        if parsed.kind in (v.TYPE_MONEY, v.TYPE_NUMBER):
            return Parsed(column_type, number=parsed.number, decimals=parsed.decimals, currency=parsed.currency,
                          grouped=parsed.grouped, marker=parsed.marker), True
        return TEXT, False
    if column_type == v.TYPE_PERCENT:
        if parsed.kind == v.TYPE_PERCENT:
            return parsed, True
        if parsed.kind in (v.TYPE_MONEY, v.TYPE_NUMBER):
            return Parsed(v.TYPE_PERCENT, number=parsed.number, decimals=parsed.decimals), True
        return TEXT, False
    return TEXT, True


def format_number(number: Decimal) -> str:
    """Canonical text of a value: no grouping, sign first, trailing zeros kept."""
    return format(number, "f")


__all__ = ["EMPTY", "ORDER_DMY", "ORDER_MDY", "Parsed", "TEXT", "classify", "clean", "date_parts", "format_number",
           "infer_column_type", "infer_date_order", "numeric_like", "parse_cell", "parse_date", "parse_number"]
