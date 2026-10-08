"""The field schema a document is held to, by its type.

Extraction asks the model for a fixed set of keys per document type (llm_service's
ENTITY_EXTRACTION_PROMPT_TEMPLATE), and models drift from it: "Vendor Name", "supplier", "vendr_name",
"invoice_total", "VAT". Each canonical field below lists the names it drifts to, so healing can map
a drifted key back without guessing beyond a close spelling.

A preset enabled in the workspace (ARCH-38, Settings → Presets) whose document type matches is used
instead: its properties, types and required list are the customer's own schema; the built-in
aliases still apply to the property names they share.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

#: Field kinds healing knows how to normalise.
STRING = "string"
NUMBER = "number"
INTEGER = "integer"
BOOLEAN = "boolean"
DATE = "date"
CURRENCY = "currency"
STRING_LIST = "string_list"
ANY_LIST = "list"
KINDS = (STRING, NUMBER, INTEGER, BOOLEAN, DATE, CURRENCY, STRING_LIST, ANY_LIST)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: str
    required: bool = False
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalSchema:
    key: str
    label: str
    fields: tuple[FieldSpec, ...] = field(default_factory=tuple)

    def by_name(self) -> dict[str, FieldSpec]:
        return {f.name: f for f in self.fields}

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.required)


_COMMERCIAL = (
    FieldSpec("vendor_name", STRING, True, (
        "vendor", "supplier", "supplier_name", "seller", "seller_name", "vendor_company", "billed_by",
        "issuer", "issuer_name", "merchant", "merchant_name",
    )),
    FieldSpec("total_amount", NUMBER, True, (
        "total", "amount", "grand_total", "invoice_total", "total_due", "amount_due", "total_amount_due",
        "balance_due", "net_payable", "total_payable", "po_total", "order_total", "invoice_amount",
    )),
    FieldSpec("currency", CURRENCY, False, ("currency_code", "curr", "ccy", "iso_currency")),
    FieldSpec("tax_amount", NUMBER, False, (
        "tax", "vat", "vat_amount", "gst", "gst_amount", "sales_tax", "tax_total", "total_tax", "igst",
    )),
    FieldSpec("subtotal", NUMBER, False, ("sub_total", "net_amount", "net_total", "amount_before_tax")),
    FieldSpec("line_items", ANY_LIST, False, (
        "items", "lines", "line_item", "products", "invoice_lines", "lineitems", "order_lines",
    )),
    FieldSpec("date", DATE, True, (
        "invoice_date", "document_date", "issue_date", "issued_on", "date_of_issue", "po_date", "order_date",
        "receipt_date", "received_date", "bill_date", "dated",
    )),
    FieldSpec("due_date", DATE, False, ("payment_due", "payment_due_date", "pay_by", "due_on")),
    FieldSpec("invoice_number", STRING, False, ("invoice_no", "invoice_num", "inv_no", "invoice_id", "bill_no")),
    FieldSpec("po_number", STRING, False, ("po_no", "purchase_order", "purchase_order_number", "order_number")),
    FieldSpec("customer_name", STRING, False, ("customer", "bill_to", "buyer", "buyer_name", "client", "sold_to")),
)

_RECEIPT = tuple(
    FieldSpec(f.name, f.kind, f.name in ("vendor_name", "date"), f.aliases) for f in _COMMERCIAL
)

BUILTIN: dict[str, CanonicalSchema] = {
    "invoice": CanonicalSchema("builtin:invoice", "Invoice", _COMMERCIAL),
    "purchase_order": CanonicalSchema(
        "builtin:purchase_order",
        "Purchase order",
        tuple(FieldSpec(f.name, f.kind, f.name in ("vendor_name", "total_amount"), f.aliases) for f in _COMMERCIAL),
    ),
    "receipt": CanonicalSchema("builtin:receipt", "Receipt", _RECEIPT),
    "resume": CanonicalSchema("builtin:resume", "Résumé", (
        FieldSpec("candidate_name", STRING, True, ("name", "full_name", "applicant_name", "candidate")),
        FieldSpec("email", STRING, False, ("email_address", "e_mail", "mail")),
        FieldSpec("phone_number", STRING, False, (
            "phone", "mobile", "telephone", "contact_number", "phone_no", "mobile_number",
        )),
        FieldSpec("core_skills", STRING_LIST, False, ("skills", "key_skills", "technical_skills", "competencies")),
        FieldSpec("degree_education", STRING, False, (
            "education", "degree", "qualification", "qualifications", "highest_degree",
        )),
        FieldSpec("years_of_experience", NUMBER, False, (
            "experience_years", "total_experience", "years_experience", "experience",
        )),
    )),
    "contract": CanonicalSchema("builtin:contract", "Contract", (
        FieldSpec("agreement_date", DATE, True, (
            "effective_date", "contract_date", "commencement_date", "start_date", "execution_date",
            "signing_date",
        )),
        FieldSpec("termination_date", DATE, False, ("end_date", "expiry_date", "expiration_date", "renewal_date")),
        FieldSpec("party_names", STRING_LIST, True, ("parties", "party", "contracting_parties", "counterparties")),
        FieldSpec("governing_law", STRING, False, ("jurisdiction", "applicable_law", "law")),
        FieldSpec("value_amount", NUMBER, False, ("contract_value", "total_value", "value", "consideration", "fee")),
        FieldSpec("agreement_number", STRING, False, ("contract_number", "agreement_no", "contract_no")),
    )),
}

#: The classifier's labels (llm_service), and common spellings, to a built-in schema.
_CLASS_TO_BUILTIN: dict[str, str] = {
    "invoice": "invoice",
    "tax_invoice": "invoice",
    "bill": "invoice",
    "purchase_order": "purchase_order",
    "po": "purchase_order",
    "receipt": "receipt",
    "goods_receipt": "receipt",
    "goods_receipt_note": "receipt",
    "resume": "resume",
    "cv": "resume",
    "curriculum_vitae": "resume",
    "contract": "contract",
    "agreement": "contract",
    "msa": "contract",
    "master_services_agreement": "contract",
}

_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize_key(key: str) -> str:
    """'Vendor Name' / 'vendorName' / 'vendor-name.' -> 'vendor_name'."""
    spaced = _CAMEL.sub(r"\1_\2", str(key or ""))
    return _NON_ALNUM.sub("_", spaced.lower()).strip("_")


def document_type_of(entities: Mapping[str, Any] | None) -> Optional[str]:
    """The classifier's label for a document, if it has one."""
    if not isinstance(entities, Mapping):
        return None
    details = entities.get("classification_details")
    if isinstance(details, Mapping):
        label = details.get("document_classification")
        if isinstance(label, str) and label.strip():
            return label.strip()
    label = entities.get("document_classification")
    return label.strip() if isinstance(label, str) and label.strip() else None


def _preset_kind(prop: Mapping[str, Any]) -> str:
    kind = str(prop.get("type") or "string")
    if kind == "number":
        return NUMBER
    if kind == "integer":
        return INTEGER
    if kind == "boolean":
        return BOOLEAN
    if kind == "array":
        items = prop.get("items") if isinstance(prop.get("items"), Mapping) else {}
        return STRING_LIST if (items or {}).get("type") == "string" else ANY_LIST
    if prop.get("format") == "date":
        return DATE
    return STRING


def _builtin_aliases() -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for schema in BUILTIN.values():
        for spec in schema.fields:
            out.setdefault(spec.name, spec.aliases)
    return out


def _from_preset(preset: Any) -> Optional[CanonicalSchema]:
    schema = preset.schema or {}
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    if not isinstance(properties, Mapping) or not properties:
        return None
    required = set(schema.get("required") or [])
    shared = _builtin_aliases()
    fields = []
    for name, prop in properties.items():
        prop = prop if isinstance(prop, Mapping) else {}
        title = normalize_key(str(prop.get("title") or ""))
        aliases = tuple(a for a in (title, *shared.get(name, ())) if a and a != name)
        kind = DATE if "date" in name and _preset_kind(prop) == STRING else _preset_kind(prop)
        fields.append(FieldSpec(str(name), kind, name in required, aliases))
    return CanonicalSchema(f"preset:{preset.id}:v{preset.version}", str(preset.label), tuple(fields))


def schema_for(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    organization_id: uuid.UUID,
    document_type: Optional[str],
    _cache: Optional[dict[str, Optional[CanonicalSchema]]] = None,
) -> Optional[CanonicalSchema]:
    """The schema a document of `document_type` is held to in this workspace, or None.

    An enabled preset for the type wins over the built-in schema. Pass the same dict as `_cache`
    for every document of one batch: the presets are read once.
    """
    if not document_type:
        return None
    key = normalize_key(document_type)
    cache = _cache if _cache is not None else {}
    if key in cache:
        return cache[key]
    from app.models.ingestion import DocumentSchemaPreset, WorkspaceSchemaPreset

    preset = db.execute(
        select(DocumentSchemaPreset)
        .join(WorkspaceSchemaPreset, WorkspaceSchemaPreset.preset_id == DocumentSchemaPreset.id)
        .where(
            WorkspaceSchemaPreset.workspace_id == workspace_id,
            WorkspaceSchemaPreset.enabled.is_(True),
            (DocumentSchemaPreset.organization_id.is_(None))
            | (DocumentSchemaPreset.organization_id == organization_id),
        )
        .order_by(DocumentSchemaPreset.version.desc())
    ).scalars()
    chosen: Optional[CanonicalSchema] = None
    for candidate in preset:
        if normalize_key(candidate.document_type) in (key, _CLASS_TO_BUILTIN.get(key)):
            chosen = _from_preset(candidate)
            if chosen is not None:
                break
    if chosen is None:
        builtin = _CLASS_TO_BUILTIN.get(key)
        chosen = BUILTIN.get(builtin) if builtin else None
    cache[key] = chosen
    return chosen


__all__ = [
    "ANY_LIST",
    "BOOLEAN",
    "BUILTIN",
    "CURRENCY",
    "CanonicalSchema",
    "DATE",
    "FieldSpec",
    "INTEGER",
    "KINDS",
    "NUMBER",
    "STRING",
    "STRING_LIST",
    "document_type_of",
    "normalize_key",
    "schema_for",
]
