"""The India pack's MSME rules: who is a micro or small supplier, and when they must be paid.

THE LAW, AS HELM APPLIES IT (a recommendation, not legal or tax advice)
======================================================================

* Micro, Small and Medium Enterprises Development Act, 2006, section 15: a buyer pays a micro or
  small supplier on or before the date agreed in writing, which may not be more than 45 days from
  the day of acceptance; with no agreement, within 15 days of acceptance.
* Income-tax Act, 1961, section 43B(h) (inserted by the Finance Act 2023): a payment to a micro or
  small enterprise made later than section 15 allows is deducted only in the year it is actually
  paid, so a late payment can lose the expense deduction for the year it was incurred.

Helm's reading, stated wherever the deadline is shown:
* **Acceptance** is the goods-receipt date where a goods receipt is matched to the invoice, else the
  invoice date (the earlier, conservative date: a later acceptance would give more time).
* **Agreed** means payment terms on the invoice or the PO of 45 days or less; anything else (no terms,
  terms Helm cannot read, or terms longer than 45 days) counts as no valid agreement: 15 days.
* A Udyam number shows the supplier is registered but not whether it is micro, small or medium.
  Until a person confirms the category (or a Udyam certificate document states it) the supplier is
  treated as **micro or small**, the choice that cannot make Helm pay late, and is shown as
  "category unconfirmed". Medium enterprises and confirmed non-MSMEs are outside both sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Optional

from app.services.helm import vocabulary as v

UDYAM = re.compile(r"\bUDYAM[\s\-–]*([A-Z]{2})[\s\-–]*(\d{2})[\s\-–]*(\d{7})\b", re.IGNORECASE)
_CATEGORY = re.compile(
    r"(?:type\s+of\s+enterprise|enterprise\s+type|category|classification)\s*[:\-]?\s*(micro|small|medium)",
    re.IGNORECASE,
)
_CERTIFICATE = re.compile(r"udyam\s+registration\s+certificate", re.IGNORECASE)

AGREED_LIMIT_DAYS = 45
DEFAULT_DAYS = 15

LAW_LINKS: tuple[dict[str, str], ...] = (
    {
        "title": "Micro, Small and Medium Enterprises Development Act, 2006, sections 15 and 16 (India Code)",
        "url": "https://www.indiacode.nic.in/",
    },
    {
        "title": "Income-tax Act, 1961, section 43B(h), inserted by the Finance Act 2023 (Income Tax Department)",
        "url": "https://incometaxindia.gov.in/",
    },
)
NOT_ADVICE = (
    "Helm gives recommendations, not legal or tax advice. Check the deadline rules with your "
    "chartered accountant."
)


def canonical_udyam(raw: str) -> Optional[str]:
    match = UDYAM.search(raw or "")
    if not match:
        return None
    state, district, serial = match.groups()
    return f"UDYAM-{state.upper()}-{district}-{serial}"


def _strings(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > 4:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item, depth + 1)


def find_udyam(entities: Optional[Mapping[str, Any]], text: Optional[str]) -> Optional[str]:
    """The supplier's Udyam number on a document, from its fields first, then its text."""
    for value in _strings(entities or {}):
        found = canonical_udyam(value)
        if found:
            return found
    return canonical_udyam(text or "")


def certificate_category(text: Optional[str]) -> Optional[str]:
    """MICRO / SMALL / MEDIUM when the document is a Udyam registration certificate that says so."""
    if not text or not _CERTIFICATE.search(text):
        return None
    match = _CATEGORY.search(text)
    return match.group(1).upper() if match else None


@dataclass(frozen=True)
class MsmeStatus:
    registered: bool
    category: Optional[str]  # MICRO, SMALL, MEDIUM, NONE, or None (unconfirmed)
    confirmed: bool
    udyam_number: Optional[str]

    @property
    def protected(self) -> bool:
        """Does section 15 / 43B(h) apply? Unconfirmed registered suppliers: yes (conservative)."""
        if self.category in (v.MSME_MEDIUM, v.MSME_NONE):
            return False
        if self.category in (v.MSME_MICRO, v.MSME_SMALL):
            return True
        return self.registered

    @property
    def label(self) -> str:
        if not self.registered and self.category is None:
            return "Not known to be an MSME"
        if self.category == v.MSME_NONE:
            return "Not an MSME (confirmed)"
        if self.category is None:
            return "MSME, category unconfirmed (treated as micro or small)"
        source = "confirmed" if self.confirmed else "from the Udyam certificate"
        return f"{self.category.capitalize()} enterprise ({source})"


@dataclass(frozen=True)
class MsmeDeadline:
    due: date
    acceptance: date
    acceptance_basis: str  # "goods receipt" | "invoice date"
    days: int
    agreed: bool
    explanation: str

    def as_json(self) -> dict[str, Any]:
        return {
            "due": self.due.isoformat(),
            "acceptance": self.acceptance.isoformat(),
            "acceptance_basis": self.acceptance_basis,
            "days": self.days,
            "agreed": self.agreed,
            "explanation": self.explanation,
        }


def deadline(
    *,
    invoice_date: Optional[date],
    goods_receipt_date: Optional[date],
    stated_net_days: Optional[int],
) -> Optional[MsmeDeadline]:
    """Section 15's payment deadline under Helm's stated reading, or None without any date."""
    if goods_receipt_date is not None and (invoice_date is None or goods_receipt_date <= invoice_date):
        acceptance, basis = goods_receipt_date, "goods receipt"
    elif invoice_date is not None:
        acceptance, basis = invoice_date, "invoice date"
    elif goods_receipt_date is not None:
        acceptance, basis = goods_receipt_date, "goods receipt"
    else:
        return None
    agreed = stated_net_days is not None and 0 <= stated_net_days <= AGREED_LIMIT_DAYS
    days = int(stated_net_days) if agreed else DEFAULT_DAYS
    if agreed:
        why = (f"{days} days from acceptance ({basis}, {acceptance.isoformat()}): the agreed terms are within "
               f"the {AGREED_LIMIT_DAYS}-day limit.")
    elif stated_net_days is None:
        why = (f"{DEFAULT_DAYS} days from acceptance ({basis}, {acceptance.isoformat()}): no payment terms were "
               "read, so the law's default applies.")
    else:
        why = (f"{DEFAULT_DAYS} days from acceptance ({basis}, {acceptance.isoformat()}): the stated "
               f"{stated_net_days} days exceed the {AGREED_LIMIT_DAYS}-day limit, so Helm uses the law's default.")
    return MsmeDeadline(due=acceptance + timedelta(days=days), acceptance=acceptance, acceptance_basis=basis,
                        days=days, agreed=agreed, explanation=why)


__all__ = ["LAW_LINKS", "MsmeDeadline", "MsmeStatus", "NOT_ADVICE", "canonical_udyam", "certificate_category",
           "deadline", "find_udyam"]
