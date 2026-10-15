"""The payment-terms parser: deterministic, and honest about what it cannot read.

It turns the text a supplier prints ("2/10 net 30", "45 days end of month", "due on receipt")
into a due date rule and, only where the text states one, an early-payment discount and a late
fee. Anything it does not recognise comes back `parsed=False` with the text kept, and **no
discount is ever assumed** for it: an assumed discount would put a number on the cockpit that no
document supports (N-085).

    >>> parse("2/10 net 30").net_days, parse("2/10 net 30").discount_pct
    (30, Decimal('2'))
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

BASIS_INVOICE = "INVOICE"  # days counted from the invoice date
BASIS_END_OF_MONTH = "END_OF_MONTH"  # days counted from the end of the invoice's month
BASIS_RECEIPT = "RECEIPT"  # due when received (on the invoice date)


@dataclass(frozen=True)
class Terms:
    raw: Optional[str]
    parsed: bool
    net_days: Optional[int] = None
    basis: str = BASIS_INVOICE
    discount_pct: Optional[Decimal] = None
    discount_days: Optional[int] = None
    #: A stated late-payment charge, as a percentage per month (an annual rate is divided by 12).
    late_fee_pct_per_month: Optional[Decimal] = None
    #: Which rule matched, for the evidence panel ("2/10 net 30").
    rule: Optional[str] = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_discount(self) -> bool:
        return self.parsed and self.discount_pct is not None and self.discount_days is not None

    def due_date(self, invoice_date: Optional[date]) -> Optional[date]:
        if invoice_date is None or not self.parsed:
            return None
        if self.basis == BASIS_RECEIPT:
            return invoice_date
        days = self.net_days or 0
        if self.basis == BASIS_END_OF_MONTH:
            last = calendar.monthrange(invoice_date.year, invoice_date.month)[1]
            return date(invoice_date.year, invoice_date.month, last) + timedelta(days=days)
        return invoice_date + timedelta(days=days)

    def discount_deadline(self, invoice_date: Optional[date]) -> Optional[date]:
        if invoice_date is None or not self.has_discount:
            return None
        return invoice_date + timedelta(days=int(self.discount_days or 0))

    def annualised_discount_rate(self) -> Optional[Decimal]:
        """The yearly rate a discount is worth: paying `net - discount_days` days early to save
        `discount_pct`. 2/10 net 30 is about 37% a year."""
        if not self.has_discount or not self.net_days:
            return None
        early_days = int(self.net_days) - int(self.discount_days or 0)
        if early_days <= 0:
            return None
        pct = Decimal(self.discount_pct or 0)
        if pct <= 0 or pct >= 100:
            return None
        rate = (pct / (Decimal(100) - pct)) * (Decimal(365) / Decimal(early_days)) * Decimal(100)
        return rate.quantize(Decimal("0.1"))

    def as_json(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "parsed": self.parsed,
            "net_days": self.net_days,
            "basis": self.basis,
            "discount_pct": str(self.discount_pct) if self.discount_pct is not None else None,
            "discount_days": self.discount_days,
            "late_fee_pct_per_month": str(self.late_fee_pct_per_month)
            if self.late_fee_pct_per_month is not None else None,
            "rule": self.rule,
            "notes": list(self.notes),
        }

    @classmethod
    def from_json(cls, data: Optional[dict[str, Any]]) -> "Terms":
        if not data:
            return UNPARSED
        def dec(value: Any) -> Optional[Decimal]:
            return Decimal(str(value)) if value is not None else None
        return cls(raw=data.get("raw"), parsed=bool(data.get("parsed")), net_days=data.get("net_days"),
                   basis=data.get("basis") or BASIS_INVOICE, discount_pct=dec(data.get("discount_pct")),
                   discount_days=data.get("discount_days"),
                   late_fee_pct_per_month=dec(data.get("late_fee_pct_per_month")), rule=data.get("rule"),
                   notes=tuple(data.get("notes") or ()))


UNPARSED = Terms(raw=None, parsed=False)

_NUM = r"(\d{1,3}(?:\.\d{1,2})?)"
_DAYS = r"(\d{1,3})"
_EOM = r"(?:e\.?o\.?m\.?|end\s+of\s+(?:the\s+)?month|month[\s-]+end)"

#: Discount + net. "2/10 net 30", "2/10, n/30", "2% 10 days net 45",
#: "2% discount if paid within 10 days, net 45", "2% within 10 days, balance in 30 days".
_DISCOUNT_NET = re.compile(
    rf"{_NUM}\s*%?\s*/\s*{_DAYS}\s*(?:days?)?\s*[,;]?\s*(?:n(?:et)?\s*/?\s*){_DAYS}(\s*(?:days?\s*)?{_EOM})?"
    rf"|{_NUM}\s*%\s*(?:discount\s*)?(?:(?:if\s+)?(?:paid\s+)?(?:within|in)\s+)?{_DAYS}\s*days?"
    rf"[^0-9%]{{0,40}}?(?:net|balance(?:\s+(?:due|in|within))?|otherwise(?:\s+net)?|full\s+amount(?:\s+in)?)\s*{_DAYS}"
    rf"(\s*(?:days?\s*)?{_EOM})?",
    re.IGNORECASE,
)
_NET_EOM = re.compile(rf"(?:net\s*|n\s*/?\s*)?{_DAYS}\s*(?:days?)?\s*(?:after\s+|from\s+)?{_EOM}"
                      rf"|{_EOM}\s*(?:\+|plus)?\s*{_DAYS}\s*(?:days?)?", re.IGNORECASE)
_NET = re.compile(rf"\bn(?:et)?\s*/?\s*-?\s*{_DAYS}\b(?:\s*days?)?", re.IGNORECASE)
_DAYS_FROM = re.compile(
    rf"(?:(?:payable|due|payment)\s+)?(?:within\s+|in\s+)?{_DAYS}\s*days?\s*"
    rf"(?:credit|(?:from|of|after)\s+(?:the\s+)?(?:date\s+of\s+)?(?:invoice|bill)(?:\s+date)?)"
    rf"|(?:payable|due|payment)\s+(?:within|in)\s+{_DAYS}\s*days?"
    rf"|credit\s+(?:period|terms?)\s*(?:of|:)?\s*{_DAYS}\s*days?"
    rf"|^\s*{_DAYS}\s*days?\s*\.?\s*$",
    re.IGNORECASE,
)
_RECEIPT = re.compile(
    r"\b(?:due|payable)\s+(?:on|upon)\s+receipt\b|\bupon\s+receipt\b|\bimmediate(?:ly)?\b"
    r"|\bcash\s+on\s+delivery\b|\bc\.?o\.?d\.?\b|\badvance\s+payment\b|\bpayment\s+in\s+advance\b",
    re.IGNORECASE,
)
_LATE_MONTHLY = re.compile(rf"{_NUM}\s*%\s*(?:per|a|/|p\.?)\s*(?:month|mo\b|m\b)", re.IGNORECASE)
_LATE_ANNUAL = re.compile(
    rf"(?:interest|late|overdue|delay)[^%]{{0,40}}?(?:@|at|of)?\s*{_NUM}\s*%\s*(?:per\s+annum|p\.?\s*a\.?|a\s+year|per\s+year|annually|yearly)"
    rf"|{_NUM}\s*%\s*(?:per\s+annum|p\.?\s*a\.?|a\s+year|per\s+year)[^.]{{0,40}}?(?:overdue|late|delay)",
    re.IGNORECASE,
)


def _dec(value: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _first(groups: tuple[Optional[str], ...]) -> list[str]:
    return [g for g in groups if g is not None]


def _late_fee(text: str) -> Optional[Decimal]:
    match = _LATE_MONTHLY.search(text)
    if match:
        return _dec(match.group(1))
    match = _LATE_ANNUAL.search(text)
    if match:
        annual = _dec(next(g for g in match.groups() if g is not None))
        if annual is not None:
            return (annual / Decimal(12)).quantize(Decimal("0.0001"))
    return None


def _sane_days(value: Optional[int]) -> bool:
    return value is not None and 0 <= value <= 365


def parse(raw: Any) -> Terms:
    """Read payment terms. Never raises; unreadable text is `parsed=False`."""
    if raw is None:
        return UNPARSED
    text = " ".join(str(raw).split())
    if not text:
        return UNPARSED
    late = _late_fee(text)

    match = _DISCOUNT_NET.search(text)
    if match:
        groups = match.groups()
        if groups[0] is not None:
            pct, discount_days, net_days, eom = groups[0], groups[1], groups[2], groups[3]
        else:
            pct, discount_days, net_days, eom = groups[4], groups[5], groups[6], groups[7]
        pct_value = _dec(pct)
        d_days = int(discount_days)
        n_days = int(net_days)
        if pct_value is not None and Decimal(0) < pct_value < Decimal(50) and _sane_days(d_days) \
                and _sane_days(n_days) and d_days <= n_days:
            return Terms(raw=text, parsed=True, net_days=n_days,
                         basis=BASIS_END_OF_MONTH if eom else BASIS_INVOICE,
                         discount_pct=pct_value.normalize(), discount_days=d_days, late_fee_pct_per_month=late,
                         rule=f"{pct_value.normalize()}/{d_days} net {n_days}" + (" EOM" if eom else ""))

    match = _NET_EOM.search(text)
    if match:
        days = int(_first(match.groups())[0])
        if _sane_days(days):
            return Terms(raw=text, parsed=True, net_days=days, basis=BASIS_END_OF_MONTH, late_fee_pct_per_month=late,
                         rule=f"{days} days end of month")

    if _RECEIPT.search(text):
        return Terms(raw=text, parsed=True, net_days=0, basis=BASIS_RECEIPT, late_fee_pct_per_month=late,
                     rule="due on receipt")

    match = _NET.search(text)
    if match:
        days = int(match.group(1))
        if _sane_days(days):
            return Terms(raw=text, parsed=True, net_days=days, late_fee_pct_per_month=late, rule=f"net {days}")

    match = _DAYS_FROM.search(text)
    if match:
        days = int(_first(match.groups())[0])
        if _sane_days(days):
            return Terms(raw=text, parsed=True, net_days=days, late_fee_pct_per_month=late,
                         rule=f"{days} days from invoice")

    return Terms(raw=text, parsed=False, late_fee_pct_per_month=late,
                 notes=("These payment terms could not be read; no discount is assumed.",))


__all__ = ["BASIS_END_OF_MONTH", "BASIS_INVOICE", "BASIS_RECEIPT", "Terms", "UNPARSED", "parse"]
