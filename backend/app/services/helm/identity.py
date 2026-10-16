"""Who "we" are, and so which way an invoice points.

An invoice whose seller is one of our identities is a receivable (`PAYMENT_EXPECTED`); any other
invoice is a payable (`PAYMENT_DUE`). An identity is a legal name and, where we have one, a tax id
(GSTIN, VAT, EIN). Tax ids decide when both sides carry one; otherwise names are compared with the
matching engine's own normaliser (`app.core.normalize.vendor_key`), so "Meridian Wholesale
Distributors Pvt Ltd" and "MERIDIAN WHOLESALE DISTRIBUTORS PRIVATE LIMITED" are the same company.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.core import normalize as nz

SELLER_NAME_KEYS: tuple[str, ...] = (
    "vendor_name", "supplier_name", "seller_name", "vendor", "supplier", "seller", "from", "billed_by",
    "company_name",
)
SELLER_TAX_KEYS: tuple[str, ...] = (
    "vendor_tax_id", "supplier_gstin", "seller_gstin", "vendor_gstin", "gstin", "gst_number", "vat_number",
    "vat_id", "tax_id", "ein", "tin", "supplier_vat",
)
BUYER_NAME_KEYS: tuple[str, ...] = (
    "bill_to", "billed_to", "buyer", "buyer_name", "customer", "customer_name", "sold_to", "invoice_to",
    "recipient", "client", "client_name",
)
BUYER_TAX_KEYS: tuple[str, ...] = (
    "buyer_gstin", "customer_gstin", "recipient_gstin", "bill_to_gstin", "buyer_tax_id", "customer_tax_id",
    "buyer_vat", "customer_vat",
)


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, Mapping):
        for key in ("name", "legal_name", "company", "value"):
            if value.get(key):
                return str(value[key]).strip() or None
        return None
    text = str(value).strip()
    return text or None


def first(entities: Optional[Mapping[str, Any]], keys: Sequence[str]) -> Optional[str]:
    if not entities:
        return None
    for key in keys:
        found = _text(entities.get(key))
        if found:
            # "Caretakers Global Inc, 1 Main St" -> the name before the first comma or newline.
            return found.split("\n")[0].strip()
    return None


def key_for(name: Optional[str], tax_id: Optional[str] = None) -> Optional[str]:
    try:
        return nz.vendor_key(name, tax_id)
    except nz.NormalizationError:
        return None


def tax_key(tax_id: Optional[str]) -> Optional[str]:
    if not tax_id:
        return None
    try:
        key = nz.vendor_key(None, tax_id)
    except nz.NormalizationError:
        return None
    return key if not key.startswith("name:") else None


@dataclass(frozen=True)
class Party:
    name: Optional[str]
    tax_id: Optional[str]

    @property
    def key(self) -> Optional[str]:
        return key_for(self.name, self.tax_id)

    @property
    def name_key(self) -> Optional[str]:
        return key_for(self.name, None) if self.name else None


def seller(entities: Optional[Mapping[str, Any]]) -> Party:
    return Party(first(entities, SELLER_NAME_KEYS), first(entities, SELLER_TAX_KEYS))


def buyer(entities: Optional[Mapping[str, Any]]) -> Party:
    return Party(first(entities, BUYER_NAME_KEYS), first(entities, BUYER_TAX_KEYS))


@dataclass(frozen=True)
class Identities:
    """Our names and tax ids, normalised once."""

    name_keys: frozenset[str]
    tax_keys: frozenset[str]

    @classmethod
    def of(cls, identities: Iterable[Mapping[str, Any]]) -> "Identities":
        names: set[str] = set()
        taxes: set[str] = set()
        for identity in identities or ():
            name_key = key_for(identity.get("name"), None) if identity.get("name") else None
            if name_key:
                names.add(name_key)
            tax = tax_key(identity.get("tax_id"))
            if tax:
                taxes.add(tax)
        return cls(frozenset(names), frozenset(taxes))

    @property
    def empty(self) -> bool:
        return not self.name_keys and not self.tax_keys

    def is_us(self, party: Party) -> bool:
        tax = tax_key(party.tax_id)
        if tax and self.tax_keys:
            return tax in self.tax_keys
        name_key = party.name_key
        return bool(name_key and name_key in self.name_keys)


def we_are_seller(entities: Optional[Mapping[str, Any]], identities: Identities) -> bool:
    if identities.empty:
        return False
    return identities.is_us(seller(entities))


def suggest(
    *,
    organization_legal_name: Optional[str],
    invoice_entities: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """First-run suggestion: the legal name, and the most frequent buyer GSTIN (with the buyer name
    printed beside it most often) on the book's invoices. An admin confirms or edits it."""
    suggestions: list[dict[str, Any]] = []
    buyer_taxes: Counter[str] = Counter()
    names_for_tax: dict[str, Counter[str]] = {}
    for entities in invoice_entities:
        party = buyer(entities)
        tax = tax_key(party.tax_id)
        if tax:
            canonical = tax.split(":", 1)[1]
            buyer_taxes[canonical] += 1
            if party.name:
                names_for_tax.setdefault(canonical, Counter())[party.name] += 1
    top_tax = buyer_taxes.most_common(1)[0][0] if buyer_taxes else None
    top_name = None
    if top_tax and names_for_tax.get(top_tax):
        top_name = names_for_tax[top_tax].most_common(1)[0][0]
    name = organization_legal_name or top_name
    if name or top_tax:
        suggestions.append({
            "name": name,
            "tax_id": top_tax,
            "tax_kind": "GSTIN" if top_tax and len(top_tax) == 15 else ("TAXID" if top_tax else None),
            "source": "SUGGESTED",
            "evidence": (f"the buyer tax id on {buyer_taxes[top_tax]} invoice(s)" if top_tax else
                         "the organization's legal name"),
        })
    return suggestions


__all__ = ["BUYER_NAME_KEYS", "BUYER_TAX_KEYS", "Identities", "Party", "SELLER_NAME_KEYS", "SELLER_TAX_KEYS",
           "buyer", "first", "key_for", "seller", "suggest", "tax_key", "we_are_seller"]
