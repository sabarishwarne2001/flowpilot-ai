"""What one document IS and STATES, read deterministically. Pure: no I/O, no model.

The input is what the platform already stored for a document: the model's extraction
(`work_items.extracted_entities`), the classifier's label, the matcher's role row and the document
text. The output is the document's twin: its kind and rank, its own identifiers and the identifiers
it references, its parties, amounts (gross and net of tax), dates, payee account, and the contractual
terms its text states (payment days, notice, delivery, liquidated damages, late interest, liability,
governing law), each with the sentence it came from.

Typed values come from the corroborator's field reader (`corroboration.fields.values_of`): amounts
through the shared parser, dates to ISO, identifiers through ARCH-31's document_number(), party
names through ARCH-42's normaliser. One reader for every engine, so TruthMesh and the corroborator
never disagree about what a document says.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Sequence

from app.services.truthmesh import vocabulary as v

MICROS = Decimal("1000000")

#: Which printed number IS the document, by kind. Everything else identifier-shaped is a reference.
OWN_NUMBER_KEYS: dict[str, tuple[str, ...]] = {
    "INVOICE": ("invoice_number", "invoice_no", "bill_number", "document_number", "number"),
    "CREDIT_NOTE": ("credit_note_number", "credit_number", "document_number", "number"),
    "PURCHASE_ORDER": ("po_number", "purchase_order_number", "order_number", "document_number", "number"),
    "GOODS_RECEIPT": ("receipt_number", "grn_number", "goods_receipt_number", "delivery_note_number",
                      "document_number"),
    "MASTER_AGREEMENT": ("agreement_number", "msa_number", "contract_number", "reference_number",
                         "document_number"),
    "CONTRACT": ("agreement_number", "contract_number", "reference_number", "document_number"),
    "AMENDMENT": ("amendment_number", "change_order_number", "variation_number"),
    "STATEMENT_OF_WORK": ("sow_number", "statement_of_work_number", "work_order_number"),
    "POLICY": ("policy_number", "policy_no", "certificate_number"),
    "INSURANCE_CLAIM": ("claim_number", "claim_no"),
    "CUSTOMS_MANIFEST": ("manifest_number", "declaration_number", "bill_of_entry_number", "entry_number"),
    "WAYBILL": ("waybill_number", "awb_number", "air_waybill_number", "bill_of_lading_number", "bol_number",
                "tracking_number", "consignment_number"),
    "CLINICAL_NOTE": ("record_number", "mrn", "medical_record_number", "encounter_number", "note_number"),
    "STATEMENT": ("statement_number", "account_number", "document_number"),
    "OTHER": ("document_number", "reference_number", "number"),
}
#: Identifier-shaped keys that are never a link between documents.
NOT_A_REFERENCE = re.compile(
    r"(tax|gstin|gst_|vat|pan\b|ein|tin\b|iban|bank|account|swift|bic|ifsc|routing|phone|mobile|fax|email|"
    r"zip|postal|pin_code|hsn|sac|sku|item|part|serial|batch|lot)"
)
_ID_KEY = re.compile(r"(_number|_no|_num|_ref|_reference|_id|^number$|^reference$|_code)$")
PAYEE_KEYS = ("iban", "vendor_iban", "bank_account", "vendor_bank_account", "bank_account_number",
              "account_number", "vendor_account_number", "beneficiary_account", "payee_account",
              "supplier_bank_account", "supplier_iban")
SUBTOTAL_CONCEPTS = ("subtotal",)
TAX_CONCEPTS = ("tax",)
DATE_SELF_KEYS = ("invoice_date", "po_date", "order_date", "receipt_date", "grn_date", "document_date",
                  "agreement_date", "date", "claim_date", "issue_date", "issued_on", "received_date",
                  "delivery_date", "amendment_date")

#: Identifier-like tokens in running text: letters and digits with separators, at least one digit and
#: one letter, 5..40 characters ("PO-E2E-5001", "MSA/2026/14", "INV2026001").
_TEXT_ID = re.compile(r"\b(?=[A-Z0-9/\-_.]*[A-Z])(?=[A-Z0-9/\-_.]*\d)[A-Z][A-Z0-9]*(?:[\-/_.][A-Z0-9]+){0,5}\b")
MAX_TEXT_REFERENCES = 40


@dataclass(frozen=True)
class DocSource:
    """Everything stored about one document that the twin is read from."""

    work_item_id: uuid.UUID
    filename: str
    entities: Mapping[str, Any]
    text: str = ""
    classification: Optional[str] = None
    role: Optional[str] = None
    role_number: Optional[str] = None
    role_currency: Optional[str] = None
    role_total_micros: Optional[int] = None
    role_date: Optional[date] = None
    #: ARCH-42: surface name -> canonical entity id, when entity resolution has run.
    entity_names: Mapping[str, str] = field(default_factory=dict)
    #: The document's mean chunk embedding (pgvector), if it has chunks.
    centroid: Optional[Sequence[float]] = None


@dataclass
class Twin:
    work_item_id: uuid.UUID
    kind: str
    kind_label: str
    rank: int
    title: str
    filename: str
    document_number: Optional[str]
    counterparty: Optional[str]
    currency: Optional[str]
    amount_micros: Optional[int]
    net_amount_micros: Optional[int]
    document_date: Optional[date]
    effective_date: Optional[date]
    end_date: Optional[date]
    due_date: Optional[date]
    identifiers: list[str]
    referenced_identifiers: list[str]
    text_references: list[str]
    parties: list[str]
    party_names: dict[str, str]
    payee_account: Optional[str]
    facts: list[dict[str, Any]]
    terms: dict[str, Any]
    centroid: Optional[Sequence[float]] = None

    @property
    def is_spend(self) -> bool:
        return self.kind in v.SPEND_KINDS

    def fingerprint(self) -> str:
        payload = {
            "k": self.kind, "n": self.document_number, "a": self.amount_micros, "na": self.net_amount_micros,
            "c": self.currency, "d": _iso(self.document_date), "e": _iso(self.effective_date),
            "x": _iso(self.end_date), "i": self.identifiers, "r": self.referenced_identifiers,
            "t": self.text_references, "p": self.parties, "b": self.payee_account, "f": self.facts,
            "terms": self.terms, "v": bool(self.centroid is not None), "engine": v.ENGINE_VERSION,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


def _fold(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def normalize_identifier(raw: Any) -> Optional[str]:
    """ARCH-31's document number, separators dropped: "PO-E2E-5001" == "PO E2E 5001" == "po/e2e/5001"."""
    from app.core import normalize as nz

    text = _fold(raw)
    if not text or len(text) > 128:
        return None
    try:
        value = re.sub(r"[^0-9A-Z]", "", nz.document_number(text))
    except Exception:  # noqa: BLE001 - an unreadable number is not an identifier
        value = re.sub(r"[^0-9A-Z]", "", text.upper())
    if len(value) < 3 or not re.search(r"\d", value):
        return None
    return value[:128]


def _words(text: Any) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip() + " "


def _matches(padded: str, words: Sequence[str]) -> bool:
    return any(f" {w} " in padded for w in words)


def kind_of(*, role: Optional[str], classification: Optional[str], filename: str,
            entities: Mapping[str, Any]) -> str:
    """The document's kind. The classifier's label decides when it names a specific kind; the
    matcher's role next; then the file name (which also refines a generic "Contract" into an MSA,
    an amendment or a statement of work); then the extraction's keys."""
    label = _words(classification)
    generic_contract = False
    for kind, words in v.KIND_KEYWORDS:
        if _matches(label, words):
            if kind == "CONTRACT":
                generic_contract = True
                break
            return kind
    name = _words(filename)
    if generic_contract or role == "CONTRACT":
        for kind in ("MASTER_AGREEMENT", "STATEMENT_OF_WORK", "AMENDMENT", "POLICY"):
            if _matches(name, dict(v.KIND_KEYWORDS)[kind]):
                return kind
        return "CONTRACT"
    if role and role in v.ROLE_KINDS:
        return v.ROLE_KINDS[role]
    for kind, words in v.KIND_KEYWORDS:
        if _matches(name, words):
            return kind
    keys = {re.sub(r"[^a-z0-9]+", "_", str(k).lower()) for k in entities}
    if "policy_number" in keys and ("sum_insured" in keys or "insured_name" in keys):
        return "POLICY"
    if "claim_number" in keys:
        return "INSURANCE_CLAIM"
    if "party_names" in keys or "agreement_number" in keys:
        return "CONTRACT"
    return "OTHER"


def _micros(number: Optional[Decimal]) -> Optional[int]:
    if number is None:
        return None
    try:
        return int((Decimal(number) * MICROS).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


def _date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    text = _fold(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    return None


def _payee(entities: Mapping[str, Any]) -> Optional[str]:
    from app.services.corroboration.fields import key_of

    flat = {key_of(k): val for k, val in (entities or {}).items()}
    for key in PAYEE_KEYS:
        value = flat.get(key)
        if isinstance(value, Mapping):
            value = value.get("value")
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            compact = re.sub(r"[^0-9A-Za-z]", "", str(value)).upper()
            if len(compact) >= 6:
                return compact
    return None


# --------------------------------------------------------------------------- terms from text

_SENTENCE = re.compile(r"[^.\n;]+(?:[.\n;]|$)")
_DAYS = r"(\d{1,3})\s*(?:\(\w+\)\s*)?(?:calendar\s+|business\s+|working\s+)?days?"


def _sentences(text: str) -> list[str]:
    body = text[: v.TEXT_SCAN_CHARS]
    return [s.strip() for s in _SENTENCE.findall(body) if len(s.strip()) > 8]


def _term(value: Any, unit: str, quote: str, source: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "quote": _fold(quote)[:240], "source": source}


def read_terms(text: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """Contractual terms the document states, with the sentence each came from."""
    terms: dict[str, Any] = {}
    payment_field = _fold(fields.get("payment_terms") or fields.get("terms_of_payment") or "")
    if payment_field:
        m = re.search(r"(?:net\s*)?(\d{1,3})", payment_field, re.I)
        if m:
            terms["payment_days"] = _term(int(m.group(1)), "days", payment_field, "field")
        elif re.search(r"(due on|upon|on) receipt|immediate", payment_field, re.I):
            terms["payment_days"] = _term(0, "days", payment_field, "field")
    law = _fold(fields.get("governing_law") or fields.get("jurisdiction") or "")
    if law:
        terms["governing_law"] = _term(law.rstrip(". "), "text", law, "field")

    for sentence in _sentences(text or ""):
        low = sentence.lower()
        if "payment_days" not in terms and re.search(r"\bpa(y|id|yment)|invoice", low):
            m = re.search(r"within\s+" + _DAYS, low) or re.search(r"\bnet\s*(\d{1,3})\b", low)
            if m:
                terms["payment_days"] = _term(int(m.group(1)), "days", sentence, "text")
        if "notice_days" not in terms and "notice" in low:
            m = re.search(_DAYS + r"[^.]{0,30}notice", low) or re.search(r"notice[^.]{0,30}?" + _DAYS, low)
            if m:
                terms["notice_days"] = _term(int(m.group(1)), "days", sentence, "text")
                d = re.search(r"before\s+(\d{4}-\d{2}-\d{2})", low)
                if d and _date(d.group(1)):
                    terms["termination_deadline"] = _term(d.group(1), "date", sentence, "text")
        if "delivery_days" not in terms and re.search(r"\bdeliver", low) and "report" not in low:
            m = re.search(r"within\s+" + _DAYS, low)
            if m:
                terms["delivery_days"] = _term(int(m.group(1)), "days", sentence, "text")
        if "liquidated_damages_pct" not in terms and re.search(r"liquidated damages|late delivery|delay", low):
            m = re.search(r"(\d+(?:\.\d+)?)\s*%[^.]{0,40}?per\s+(day|week|month)", low)
            if m:
                terms["liquidated_damages_pct"] = _term(float(m.group(1)), f"%/{m.group(2)}", sentence, "text")
        if "late_interest_pct" not in terms and re.search(r"interest|late payment|overdue", low):
            m = re.search(r"(\d+(?:\.\d+)?)\s*%[^.]{0,30}?per\s+(month|annum|year)", low)
            if m:
                terms["late_interest_pct"] = _term(float(m.group(1)), f"%/{m.group(2)}", sentence, "text")
        if "liability_cap_months" not in terms and "liabilit" in low:
            m = re.search(r"(\d{1,3})\s*months?\s+of\s+(?:fees|charges)", low)
            if m:
                terms["liability_cap_months"] = _term(int(m.group(1)), "months", sentence, "text")
        if "governing_law" not in terms:
            m = re.search(r"governed by (?:and construed in accordance with )?the laws? of ([^.;,]+)", sentence, re.I)
            if m:
                terms["governing_law"] = _term(m.group(1).strip(), "text", sentence, "text")
        if "end_date" not in terms and re.search(r"renewal date|expir|terminat|end date|until", low):
            m = re.search(r"(?:renewal date|expiry date|expiration date|end date|expires on|until)\s*:?\s*"
                          r"(\d{4}-\d{2}-\d{2})", low)
            if m and _date(m.group(1)):
                terms["end_date"] = _term(m.group(1), "date", sentence, "text")
        if "effective_date" not in terms and re.search(r"effective|commenc", low):
            m = re.search(r"(?:effective date|commencement date|effective from|effective)\s*:?\s*(\d{4}-\d{2}-\d{2})", low)
            if m and _date(m.group(1)):
                terms["effective_date"] = _term(m.group(1), "date", sentence, "text")
    return terms


def text_references(text: str, own: Sequence[str]) -> list[str]:
    """Identifier-shaped tokens the text mentions, normalised; the document's own numbers excluded."""
    seen: list[str] = []
    mine = set(own)
    for match in _TEXT_ID.finditer((text or "")[: v.TEXT_SCAN_CHARS].upper()):
        token = match.group(0)
        if len(token) < 5 or len(token) > 40:
            continue
        value = normalize_identifier(token)
        if value and value not in mine and value not in seen:
            seen.append(value)
        if len(seen) >= MAX_TEXT_REFERENCES:
            break
    return seen


# --------------------------------------------------------------------------- the twin

def read(source: DocSource) -> Twin:
    from app.core import normalize as nz
    from app.services.corroboration import fields as F
    from app.services.entities import normalize as en

    entities: Mapping[str, Any] = source.entities if isinstance(source.entities, Mapping) else {}
    kind = kind_of(role=source.role, classification=source.classification, filename=source.filename,
                   entities=entities)
    label, rank = v.KINDS.get(kind, v.KINDS["OTHER"])
    values = F.values_of(entities, source.entity_names)
    flat = F.flatten(entities)

    # ---- identifiers: own vs referenced
    own_keys = OWN_NUMBER_KEYS.get(kind, OWN_NUMBER_KEYS["OTHER"])
    own: list[str] = []
    display_number: Optional[str] = None
    for key in own_keys:
        if key in flat and not isinstance(flat[key], list):
            value = normalize_identifier(flat[key])
            if value and value not in own:
                own.append(value)
                display_number = display_number or _fold(flat[key])[:128]
    if source.role_number and kind == v.ROLE_KINDS.get(source.role or "", ""):
        value = normalize_identifier(source.role_number)
        if value and value not in own:
            own.insert(0, value)
            display_number = display_number or source.role_number
    references: list[str] = []
    for key, raw in sorted(flat.items()):
        leaf = key.rsplit(".", 1)[-1]
        if leaf in own_keys or isinstance(raw, list) or NOT_A_REFERENCE.search(leaf):
            continue
        fv = values.get(leaf) or next((x for x in values.values() if x.key == key), None)
        identifier_like = (fv is not None and fv.klass == "IDENTIFIER") or bool(_ID_KEY.search(leaf))
        if not identifier_like:
            continue
        value = normalize_identifier(raw)
        if value and value not in own and value not in references:
            references.append(value)
    mentioned = [t for t in text_references(source.text, own) if t not in references]

    # ---- parties
    parties: list[str] = []
    party_names: dict[str, str] = {}

    def _party(surface: Any) -> None:
        name = _fold(surface)
        if not name or len(name) > 300:
            return
        key = en.normalize_organization(name) or name.lower()
        if key and key not in parties:
            parties.append(key)
            party_names[key] = name

    vendor_value = values.get("vendor")
    for concept in ("vendor", "buyer", "insured"):
        if concept in values:
            _party(values[concept].display)
    for key in ("party_names", "parties", "contracting_parties"):
        raw = flat.get(key)
        if isinstance(raw, list):
            for item in raw:
                _party(item)
    counterparty = vendor_value.display if vendor_value else (party_names[parties[-1]] if parties else None)

    # ---- money
    currency = nz.currency_code(source.role_currency) if source.role_currency else None
    if currency is None and "currency" in values:
        currency = nz.currency_code(values["currency"].display)
    total = values.get("total")
    amount = source.role_total_micros if source.role_total_micros is not None else (
        _micros(total.number) if total is not None else None)
    subtotal = values.get("subtotal")
    tax = values.get("tax")
    net: Optional[int]
    if subtotal is not None and subtotal.number is not None:
        net = _micros(subtotal.number)
    elif amount is not None and tax is not None and tax.number is not None:
        net = amount - (_micros(tax.number) or 0)
    else:
        net = amount

    # ---- dates
    document_date = source.role_date
    if document_date is None:
        for key in DATE_SELF_KEYS:
            parsed = _date(values.get(f"self:{key}").normalized) if values.get(f"self:{key}") else None
            if parsed:
                document_date = parsed
                break
    terms = read_terms(source.text, flat)
    effective = _date(values["effective_date"].normalized) if "effective_date" in values else None
    end = _date(values["termination_date"].normalized) if "termination_date" in values else None
    if effective is None and "effective_date" in terms:
        effective = _date(terms["effective_date"]["value"])
    if end is None and "end_date" in terms:
        end = _date(terms["end_date"]["value"])
    if kind in v.AGREEMENT_KINDS and effective is None:
        effective = document_date
    due = _date(values["self:due_date"].normalized) if "self:due_date" in values else None
    if due is None and "key:due_date" in values:
        due = _date(values["key:due_date"].normalized)

    # ---- facts (what the conflict hunter and the matrix compare), stable order
    facts: list[dict[str, Any]] = []
    for name, fv in sorted(values.items()):
        if fv.klass == "LIST" and name != "parties":
            continue
        facts.append({"concept": name, "label": F.label_of(name), "class": fv.klass, "cross": fv.cross,
                      "key": fv.key, "display": fv.display[:240], "normalized": (fv.normalized or "")[:240],
                      "number": str(fv.number) if fv.number is not None else None})

    title = display_number or source.filename
    return Twin(
        work_item_id=source.work_item_id, kind=kind, kind_label=label, rank=rank, title=title[:300],
        filename=source.filename, document_number=display_number, counterparty=(counterparty or None),
        currency=currency, amount_micros=amount, net_amount_micros=net, document_date=document_date,
        effective_date=effective, end_date=end, due_date=due, identifiers=own, referenced_identifiers=references,
        text_references=mentioned, parties=parties, party_names=party_names, payee_account=_payee(entities),
        facts=facts, terms=terms, centroid=source.centroid,
    )


__all__ = ["DocSource", "Twin", "kind_of", "normalize_identifier", "read", "read_terms", "text_references"]
