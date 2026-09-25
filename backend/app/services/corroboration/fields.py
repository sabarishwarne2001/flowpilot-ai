"""ARCH45-S1:fields — extracted values compared across documents. Pure; no I/O.

`work_items.extracted_entities` is model output keyed by whatever the prompt
or an ARCH-38 preset asked for. Documents of different kinds name the same
fact differently (a PO's `po_number` is an invoice's `po_reference`), and some
facts are the document's own identity (an invoice's number and date) which
naturally differ between a PO and its invoice. So every key is mapped to a
CONCEPT:

  cross-document concepts   vendor, buyer, PO reference, totals, tax, currency,
                            parties, governing law, effective / termination dates:
                            compared across every document that carries them,
                            and an absence is reported (informational)
  self concepts             the document's own number and date: compared only
                            between documents that use the same key (two
                            versions of one invoice), never PO against invoice
  anything else             compared between documents carrying the same key

Values are typed before comparison: amounts through the ARCH-44 parser
(Indian, Western, European grouping) with tolerance, dates to ISO, document
numbers through ARCH-31's document_number(), names through ARCH-42's name
normaliser -- and two names are EQUAL when ARCH-42 resolved them to the same
canonical entity ("Acme Ltd" / "ACME LIMITED"), which is the entity graph
doing exactly the job it was built for.

Generic tag lists (people, organizations, dates, key_metadata_tags, ...) are
not compared: they are search aids, not commitments, and the entity and clause
layers carry the same information properly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from typing import Any, Mapping, Optional, Sequence

from app.services.corroboration import materiality as mat
from app.services.corroboration import normalize as n
from app.services.corroboration import vocabulary as v

#: concept -> (class, cross-document?, keys)
CONCEPTS: dict[str, tuple[str, bool, tuple[str, ...]]] = {
    "vendor": ("PARTY", True, ("vendor_name", "supplier_name", "seller_name", "vendor", "supplier", "seller",
                               "billed_by", "service_provider", "consignor")),
    "insured": ("PARTY", True, ("insured_name", "name_of_insured", "insured", "policyholder", "policy_holder",
                                "patient_name", "claimant_name")),
    "buyer": ("PARTY", True, ("buyer_name", "customer_name", "buyer", "customer", "purchaser", "bill_to", "billed_to",
                              "sold_to", "client_name", "client")),
    "po_reference": ("IDENTIFIER", True, ("po_number", "purchase_order_number", "po_reference", "po_ref",
                                          "buyer_order_number", "reference_po", "purchase_order", "order_number",
                                          "po_no")),
    "total": ("MONEY", True, ("total_amount", "grand_total", "total", "invoice_total", "amount_due", "net_payable",
                              "total_value", "value_amount", "contract_value", "order_total", "total_payable")),
    "subtotal": ("MONEY", True, ("subtotal", "sub_total", "taxable_value", "taxable_amount", "net_amount")),
    "tax": ("MONEY", True, ("tax_amount", "tax", "gst_amount", "vat_amount", "total_tax", "igst", "cgst", "sgst")),
    "currency": ("TEXT", True, ("currency", "currency_code", "ccy")),
    "parties": ("LIST", True, ("party_names", "parties", "contracting_parties")),
    "governing_law": ("TEXT", True, ("governing_law", "jurisdiction")),
    "effective_date": ("DATE", True, ("effective_date", "start_date", "commencement_date", "policy_start_date")),
    "termination_date": ("DATE", True, ("termination_date", "end_date", "expiry_date", "expiration_date",
                                        "policy_end_date")),
    "payment_terms": ("TEXT", True, ("payment_terms", "terms_of_payment", "credit_terms")),
    "policy_number": ("IDENTIFIER", True, ("policy_number", "policy_no")),
    "sum_insured": ("MONEY", True, ("sum_insured", "coverage_limit", "insured_amount")),
    "deductible": ("MONEY", True, ("deductible", "excess")),
}
#: The document's own identity: compared only under the SAME key.
SELF_KEYS = frozenset({"invoice_number", "invoice_no", "bill_number", "grn_number", "receipt_number",
                       "document_number", "delivery_note_number", "dn_number", "number", "invoice_date", "grn_date",
                       "receipt_date", "document_date", "date", "agreement_date", "amendment_date", "po_date",
                       "order_date", "delivery_date", "claim_number", "claim_date"})
SKIP_KEYS = frozenset({"people", "organizations", "organisations", "locations", "dates", "emails", "phone_numbers",
                       "urls", "key_metadata_tags", "core_entities_mentioned", "document_classification",
                       "document_type", "classification", "summary", "language", "confidence", "core_skills",
                       "keywords", "tags", "notes"})
LINE_KEYS = frozenset({"line_items", "lineitems", "line_item", "items", "invoice_lines", "lines", "products",
                       "details"})
_KEY_TO_CONCEPT = {key: concept for concept, (_, _, keys) in CONCEPTS.items() for key in keys}
_MONEY_WORDS = re.compile(r"(amount|total|price|value|fee|rent|premium|deductible|limit|cost|balance|tax|charge)")
_DATE_WORDS = re.compile(r"(date|dated|_on$|expiry|until)")
_ID_WORDS = re.compile(r"(number|_no$|^no$|_id$|registration|reference|_ref$|code$|gstin|pan$|iban|account)")


def key_of(raw: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(raw).strip())
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def flatten(fields: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Scalar leaves (and lists of scalars) keyed by dotted snake_case paths."""
    out: dict[str, Any] = {}
    for raw_key, value in (fields or {}).items():
        key = key_of(raw_key)
        if not key or key.startswith("_") or key in SKIP_KEYS or (not prefix and key in LINE_KEYS):
            continue
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            if "value" in value and not isinstance(value.get("value"), (Mapping, list)):
                out[path] = value.get("value")
            else:
                out.update(flatten(value, prefix=f"{path}."))
        elif isinstance(value, (list, tuple)):
            if all(not isinstance(x, (Mapping, list, tuple)) for x in value):
                items = [x for x in value if x is not None and str(x).strip()]
                if items:
                    out[path] = list(items)
        elif value is not None and str(value).strip():
            out[path] = value
    return out


@dataclass(frozen=True)
class FieldValue:
    concept: str
    klass: str
    cross: bool
    key: str
    display: str
    normalized: Optional[str]
    number: Optional[Decimal] = None
    #: ARCH-42 canonical entity id when this value names a resolved party.
    entity: Optional[str] = None


def _classify_unknown(key: str, value: Any) -> str:
    from app.services.tables import values as vals

    if isinstance(value, list):
        return "LIST"
    if isinstance(value, bool):
        return "TEXT"
    text = n.fold(str(value))
    if _ID_WORDS.search(key) and not isinstance(value, (int, float, Decimal)):
        return "IDENTIFIER"
    parsed = vals.classify(text)
    if parsed.kind == "DATE" or (_DATE_WORDS.search(key) and n.value_tokens(text) and
                                 n.value_tokens(text)[0].kind == "DATE"):
        return "DATE"
    if parsed.kind in ("MONEY", "NUMBER", "PERCENT") or isinstance(value, (int, float, Decimal)):
        return "MONEY" if _MONEY_WORDS.search(key) else "NUMBER"
    tokens = n.value_tokens(text)
    if tokens and tokens[0].kind == "MONEY" and len(tokens) == 1:
        return "MONEY"
    return "TEXT"


def _normalize(klass: str, value: Any) -> tuple[Optional[str], Optional[Decimal]]:
    from app.core import normalize as nz
    from app.services.entities import normalize as en

    if klass == "LIST":
        items = sorted({n.canonical(str(x), strip_number=False) for x in value})
        return "|".join(items), None
    text = n.fold(str(value))
    if klass in ("MONEY", "NUMBER"):
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            number = Decimal(str(value))
            return n.plain(number), number
        tokens = [t for t in n.value_tokens(text) if t.number is not None]
        if tokens:
            return n.plain(tokens[0].number), tokens[0].number
        return n.canonical(text, strip_number=False), None
    if klass == "DATE":
        tokens = [t for t in n.value_tokens(text) if t.when is not None]
        return (tokens[0].when.isoformat() if tokens else n.canonical(text, strip_number=False)), None
    if klass == "IDENTIFIER":
        # ARCH-31's document_number() (labels dropped, leading zeros stripped per
        # numeric run), then separators dropped: "POL-2026-000123" = "POL/2026/123",
        # "TN 09 AB 1234" = "TN09AB1234"; "INV-2026-1" != "INV-20260001".
        try:
            return re.sub(r"[^0-9A-Z]", "", nz.document_number(text)), None
        except Exception:  # noqa: BLE001 - an unreadable identifier compares as text
            return n.canonical(text, strip_number=False), None
    if klass == "PARTY":
        return en.normalize_organization(text) or n.canonical(text, strip_number=False), None
    return n.canonical(text, strip_number=False), None


def name_key(surface: str) -> str:
    """The key under which a surface name is looked up in the entity map."""
    return n.canonical(surface, strip_number=False)


def values_of(fields: Mapping[str, Any], entity_names: Mapping[str, str]) -> dict[str, FieldValue]:
    """concept (or self/other key) -> the document's value."""
    out: dict[str, FieldValue] = {}
    for key, value in sorted(flatten(fields).items()):
        leaf = key.rsplit(".", 1)[-1]
        concept = _KEY_TO_CONCEPT.get(leaf) if "." not in key else None
        if concept is not None:
            klass, cross, _ = CONCEPTS[concept]
            name = concept
        else:
            klass, cross = _classify_unknown(leaf, value), False
            name = f"self:{key}" if leaf in SELF_KEYS and "." not in key else f"key:{key}"
        if name in out:
            continue  # the first alias in key order wins; aliases are the same fact
        display = ", ".join(str(x) for x in value) if isinstance(value, list) else n.fold(str(value))
        normalized, number = _normalize(klass, value)
        entity = entity_names.get(name_key(display)) if klass == "PARTY" else None
        out[name] = FieldValue(name, klass, cross, key, display[:500], normalized, number, entity)
    return out


@dataclass
class FieldFinding:
    kind: str
    concept: str
    label: str
    materiality: float
    values: dict[int, Optional[FieldValue]]
    detail: dict


def _equal(a: FieldValue, b: FieldValue, *, money_tol: Decimal, rel_tol: Decimal) -> bool:
    if a.number is not None and b.number is not None:
        diff = abs(a.number - b.number)
        if diff <= money_tol:
            return True
        top = max(abs(a.number), abs(b.number))
        return bool(rel_tol > 0 and top > 0 and diff / top <= rel_tol)
    if a.entity is not None and b.entity is not None:
        return a.entity == b.entity  # ARCH-42 decided whether these name one party
    if a.klass == "PARTY" and a.normalized and b.normalized:
        from app.services.entities import normalize as en

        return a.normalized == b.normalized or en.jaro_winkler(a.normalized, b.normalized) >= PARTY_NAME_EQUAL
    return a.normalized == b.normalized


#: Two party names that no entity resolution links are the same party at or
#: above this Jaro-Winkler similarity of their normalised forms.
PARTY_NAME_EQUAL = 0.96


def label_of(concept: str) -> str:
    name = concept.split(":", 1)[-1]
    return name.replace(".", " › ").replace("_", " ").capitalize()


def compare(per_doc: Sequence[dict[str, FieldValue]], *, money_tol: Decimal, rel_tol: Decimal) -> list[FieldFinding]:
    """Findings over documents given in canonical order (index = canonical doc index)."""
    concepts = sorted({c for doc in per_doc for c in doc})
    findings: list[FieldFinding] = []
    for concept in concepts:
        holders = {d: doc[concept] for d, doc in enumerate(per_doc) if concept in doc}
        sample = next(iter(holders.values()))
        if len(holders) >= 2:
            unequal = [(a, b) for a, b in combinations(sorted(holders), 2)
                       if not _equal(holders[a], holders[b], money_tol=money_tol, rel_tol=rel_tol)]
            if unequal:
                base = mat.FIELD_BASE.get(sample.klass, 0.5)
                if sample.klass in ("MONEY", "NUMBER"):
                    rels = [mat.relative(holders[a].number, holders[b].number) for a, b in unequal
                            if holders[a].number is not None and holders[b].number is not None]
                    score = max((mat.scaled(base, r) for r in rels), default=base)
                else:
                    score = base
                findings.append(FieldFinding(
                    v.KIND_FIELD_MISMATCH, concept, label_of(concept), score,
                    {d: holders.get(d) for d in range(len(per_doc))},
                    {"class": sample.klass, "disagreeing_pairs": [[a, b] for a, b in unequal]}))
        if sample.cross and 0 < len(holders) < len(per_doc):
            findings.append(FieldFinding(
                v.KIND_FIELD_MISSING, concept, label_of(concept), mat.FIELD_MISSING,
                {d: holders.get(d) for d in range(len(per_doc))},
                {"class": sample.klass, "missing_in": [d for d in range(len(per_doc)) if d not in holders]}))
    return findings


__all__ = ["CONCEPTS", "FieldFinding", "FieldValue", "PARTY_NAME_EQUAL", "SELF_KEYS", "SKIP_KEYS", "compare",
           "flatten", "key_of", "label_of", "name_key", "values_of"]
