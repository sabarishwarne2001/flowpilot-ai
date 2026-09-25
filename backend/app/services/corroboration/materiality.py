"""ARCH45-S1:materiality — how much a difference matters. Pure; no I/O.

    materiality = importance of the subject  x  magnitude of the change      (clamped to [0, 1])

IMPORTANCE of a clause is the heaviest class of words it contains (payment
and price 1.0, liability and indemnity 1.0, term / termination / notice 0.9,
coverage and exclusions 0.9, delivery and quantity 0.8, governing law and
disputes 0.8, confidentiality and assignment 0.7, anything else 0.5).

MAGNITUDE of a change: a value that moved (an amount, a date, a count of
days, a percentage) or an obligation that flipped ("shall" -> "shall not") is
1.0 and never scores below VALUE_FLOOR; rewording is at most 0.45, in
proportion to how much of the text changed (so, at the default threshold,
rewording alone is informational, never material); an absent clause is 0.85.

Amounts and quantities scale with the relative difference, reaching full
weight at 5%:  base * (0.6 + 0.4 * min(1, rel / 0.05)).

Severity bands (ck_discrepancies_severity): HIGH >= 0.75, MEDIUM >= 0.5,
LOW below. MATERIAL means at or above the run's threshold (default 0.5), so
with the defaults every value, obligation, party and line change is material
and pure rewording, formatting and absent optional fields are informational.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Optional

from app.services.corroboration import vocabulary as v

_Q = Decimal("0.0001")
VALUE_FLOOR = 0.6
MISSING_CLAUSE = 0.85
MAX_WORDING = 0.45
FULL_AT_RELATIVE = 0.05

#: (weight, pattern). The heaviest class present wins.
IMPORTANCE_CLASSES: tuple[tuple[float, re.Pattern[str]], ...] = (
    (1.0, re.compile(r"\b(pay(?:able|ment|s)?|paid|price[sd]?|fees?|consideration|rent(?:al)?|amount|invoice[sd]?|"
                     r"charges?|cost|rate|salary|compensation|premium|interest|penalt(?:y|ies)|tax(?:es)?|gst|vat)\b",
                     re.I)),
    (1.0, re.compile(r"\b(liabilit(?:y|ies)|liable|indemnif\w*|indemnit\w*|damages|warrant(?:y|ies)|guarantee\w*|"
                     r"limitation|cap)\b", re.I)),
    (0.9, re.compile(r"\b(term|terminat\w*|renew\w*|notice|expir\w*|effective|duration|commence\w*|date:|"
                     r"deadline|within)\b", re.I)),
    (0.9, re.compile(r"\b(cover(?:age|ed)?|exclu\w*|deductible|insured|sum insured|claim\w*|benefit\w*)\b", re.I)),
    (0.8, re.compile(r"\b(deliver\w*|quantit(?:y|ies)|specification\w*|acceptance|inspection|quality|shipment|"
                     r"units?|tolerance)\b", re.I)),
    (0.8, re.compile(r"\b(governing law|jurisdiction|arbitration|dispute\w*|courts?)\b", re.I)),
    (0.7, re.compile(r"\b(confidential\w*|assign\w*|intellectual property|non-compete|exclusiv\w*|"
                     r"subcontract\w*|data protection|privacy)\b", re.I)),
)
DEFAULT_IMPORTANCE = 0.5

#: Field concept classes -> base materiality of a disagreement.
FIELD_BASE = {"MONEY": 0.9, "IDENTIFIER": 0.8, "PARTY": 0.8, "DATE": 0.75, "NUMBER": 0.7, "TEXT": 0.5, "LIST": 0.4}
FIELD_MISSING = 0.3
ENTITY_PARTY = 0.9
ENTITY_OTHER = 0.6
ENTITY_MISSING = 0.3
LINE_NUMERIC = 0.9
LINE_CODE = 0.7
LINE_WORDING = 0.3
LINE_MISSING_BASE = 0.6
LINE_MISSING_UNPRICED = 0.75
RULE_CONFLICT = 0.9
RULE_FAILED = 0.75
RULE_VALUE = 0.6

PARTY_ROLES = frozenset({"vendor", "supplier", "seller", "buyer", "customer", "purchaser", "landlord", "tenant",
                         "lessor", "lessee", "employer", "employee", "insurer", "insured", "policyholder",
                         "claimant", "consignee", "consignor", "shipper", "party", "contractor", "client",
                         "licensor", "licensee", "borrower", "lender", "guarantor", "patient", "provider"})


def q(value: float) -> Decimal:
    return Decimal(str(max(0.0, min(1.0, value)))).quantize(_Q, rounding=ROUND_HALF_EVEN)


def importance(*texts: Optional[str]) -> float:
    joined = " ".join(t for t in texts if t)
    return max([w for w, rx in IMPORTANCE_CLASSES if rx.search(joined)], default=DEFAULT_IMPORTANCE)


def relative(a: Decimal, b: Decimal) -> float:
    top = max(abs(a), abs(b))
    if top == 0:
        return 0.0
    return float(abs(a - b) / top)


def scaled(base: float, rel: float) -> float:
    return base * (0.6 + 0.4 * min(1.0, rel / FULL_AT_RELATIVE))


def clause_modified(imp: float, change: str, similarity: float) -> float:
    if change in (v.CHANGE_VALUE, v.CHANGE_NEGATION):
        return max(VALUE_FLOOR, imp)
    return imp * min(MAX_WORDING, 2.0 * (1.0 - similarity))


def clause_missing(imp: float) -> float:
    return imp * MISSING_CLAUSE


def severity(materiality: Decimal) -> str:
    if materiality >= Decimal(v.HIGH_FROM):
        return v.SEVERITY_HIGH
    if materiality >= Decimal(v.MEDIUM_FROM):
        return v.SEVERITY_MEDIUM
    return v.SEVERITY_LOW


__all__ = ["DEFAULT_IMPORTANCE", "FIELD_BASE", "IMPORTANCE_CLASSES", "PARTY_ROLES", "clause_missing",
           "clause_modified", "importance", "q", "relative", "scaled", "severity"]
