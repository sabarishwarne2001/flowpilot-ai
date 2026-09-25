"""ARCH44-S1:roles — what each column means. Pure; no I/O.

A column's role comes, in order of precedence, from a reviewer (set on the
table), from a mapping learned for this layout (app/services/tables/memory.py,
Wilson-bounded), or from the header words below, with the column's value type
as the last resort. Roles steer validation (a BALANCE column is tried first as
the running balance) and name the fields of the JSON export.
"""

from __future__ import annotations

import hashlib
import re
from typing import Mapping, Optional, Sequence

from app.services.tables import vocabulary as v

#: Checked in order: the specific phrases before the general words they contain.
_RULES: tuple[tuple[str, re.Pattern], ...] = tuple((role, re.compile(pattern, re.I)) for role, pattern in (
    (v.ROLE_VALUE_DATE, r"\bvalue\s*(date|dt)\b"),
    (v.ROLE_RANGE, r"\b(reference\s+range|ref\.?\s*range|normal\s+range|bio\.?\s*ref|range|limits?)\b"),
    (v.ROLE_UNIT_PRICE, r"\b(unit\s*price|unit\s*cost|rate|price|mrp)\b"),
    (v.ROLE_BALANCE, r"\b(balance|bal)\b"),
    (v.ROLE_DEBIT, r"\b(withdrawals?|debits?|dr|paid\s*out)\b"),
    (v.ROLE_CREDIT, r"\b(deposits?|credits?|cr|paid\s*in)\b"),
    (v.ROLE_DATE, r"\b(date|dt|dated)\b"),
    (v.ROLE_REFERENCE, r"\b(ref|reference|chq|cheque|utr|txn\s*id|transaction\s*id|voucher|instrument)\b"),
    (v.ROLE_DESCRIPTION, r"\b(narration|description|particulars|details|remarks|item|items|test|investigation|name|service|product)\b"),
    (v.ROLE_QUANTITY, r"\b(qty|quantity|nos|units)\b"),
    (v.ROLE_TAX, r"\b(tax|gst|vat|igst|cgst|sgst|cess)\b"),
    (v.ROLE_DISCOUNT, r"\b(discount|disc)\b"),
    (v.ROLE_TOTAL, r"\b(total|net)\b"),
    (v.ROLE_AMOUNT, r"\b(amount|amt|value)\b"),
    (v.ROLE_CODE, r"\b(code|hsn|sac|sku|s\.?\s*no|sr\.?\s*no|sl\.?\s*no|id)\b"),
    (v.ROLE_UNIT, r"\b(unit|uom)\b"),
    (v.ROLE_PERCENT, r"(%|\bpercent\b|\bpct\b)"),
))

_BY_TYPE = {v.TYPE_DATE: v.ROLE_DATE, v.TYPE_MONEY: v.ROLE_AMOUNT, v.TYPE_PERCENT: v.ROLE_PERCENT}

_NORM_DIGIT = re.compile(r"\d")
_NORM_PUNCT = re.compile(r"[^\w#% ]+")
_NORM_SPACE = re.compile(r"\s+")


def normalise_label(text: str) -> str:
    """Lower case, digits masked, punctuation dropped: 'Withdrawal Amt.' -> 'withdrawal amt'."""
    text = _NORM_DIGIT.sub("#", (text or "").lower())
    return _NORM_SPACE.sub(" ", _NORM_PUNCT.sub(" ", text)).strip()


def header_key(path: Sequence[str]) -> str:
    return " / ".join(normalise_label(p) for p in path if normalise_label(p))[:200]


def layout_key(paths: Sequence[Sequence[str]]) -> str:
    """The table layout: its column header keys in order (SHA-1 hex, 40 chars)."""
    return hashlib.sha1("|".join(header_key(p) for p in paths).encode("utf-8")).hexdigest()


def infer_role(path: Sequence[str], value_type: str) -> str:
    label = " ".join(path)
    for role, pattern in _RULES:
        if pattern.search(label):
            return role
    return _BY_TYPE.get(value_type, v.ROLE_OTHER)


def infer_roles(paths: Sequence[Sequence[str]], types: Sequence[str],
                learned: Optional[Mapping[str, str]] = None) -> list[tuple[str, str]]:
    """(role, source) per column. `learned` maps header keys to learned roles."""
    out: list[tuple[str, str]] = []
    for path, value_type in zip(paths, types):
        key = header_key(path)
        if learned and key and learned.get(key) in v.ROLES:
            out.append((learned[key], v.ROLE_SOURCE_LEARNED))
        else:
            out.append((infer_role(path, value_type), v.ROLE_SOURCE_INFERRED))
    return out


_SLUG = re.compile(r"[^a-z0-9]+")


def column_keys(paths: Sequence[Sequence[str]]) -> list[str]:
    keys: list[str] = []
    for i, path in enumerate(paths):
        base = "_".join(s for s in (_SLUG.sub("_", normalise_label(p).replace("#", "n")).strip("_") for p in path) if s)
        base = base[:60] or f"col_{i + 1}"
        key, n = base, 2
        while key in keys:
            key, n = f"{base}_{n}", n + 1
        keys.append(key)
    return keys


__all__ = ["column_keys", "header_key", "infer_role", "infer_roles", "layout_key", "normalise_label"]
