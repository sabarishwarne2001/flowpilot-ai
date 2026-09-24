"""ARCH42-S1:annotations — which extracted fields name an entity. Pure.

THE `x-entity` VOCABULARY
=========================

A preset schema property may carry `x-entity`:

  {"kind": "PERSON", "role": "candidate"}
      the field's value names an entity (a list names one per element)
  {"identifier": "EMAIL", "of": "candidate_name"}
      the value is an identifier of the entity another field names
  {"kind": "SHIPMENT", "role": "shipment", "identifier": "BL_NUMBER"}
      the value is BOTH the entity's name and its identifier (goods, accounts)
  "edges": [{"relation": "EMPLOYED_BY", "from": "candidate_name"}]
      a relationship; "from" X means X -> this, "to" X means this -> X
  "pairwise": "CONTRACTED_WITH"
      on a list field: every pair of elements is related

and a schema may carry `x-entity-detect`:

  {"of": "holder_name", "identifiers": ["AADHAAR", "PAN"]}
      find these identifiers in the document TEXT with their checksums.
      The Aadhaar/PAN card preset deliberately extracts no number
      ("Numbers are redacted by default"), so the number never enters
      work_items.extracted_entities; resolution reads it from the text and
      stores only its HMAC and the last four digits.

WHERE ANNOTATIONS COME FROM (a correction to the roadmap)
=========================================================

ARCH-38 presets are a catalogue a workspace applies and enables; before
ARCH-42 nothing sent a preset's fields to the extraction prompt, so preset
keys only appeared by coincidence. ARCH-42 therefore reads annotations from
two places, in one vocabulary:

  1. the preset that fits the document (`presets.select_for_document`), and
     ARCH-42 wires ENABLED presets into the extraction prompt so their keys
     actually appear (`presets.prompt_context`);
  2. BUILTIN_ANNOTATIONS, for the keys the platform's generic extraction
     prompt already produces (vendor_name, candidate_name, party_names...),
     so invoices, résumés and contracts resolve with no preset at all.

A preset's annotation wins over the built-in one for the same field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from app.services.entities import normalize as n
from app.services.entities import vocabulary as v

ANNOTATION_KEY = "x-entity"
DETECT_KEY = "x-entity-detect"


def _subject(kind: str, role: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "role": role, **extra}


def _identifier(kind: str, of: str) -> dict[str, Any]:
    return {"identifier": kind, "of": of}


#: ARCH42-S1:preset-annotations. What arch42_step1_entity_graph writes into the
#: 11 platform presets; verify_arch42 asserts the two copies are identical.
PRESET_ENTITY_ANNOTATIONS: dict[str, dict[str, Any]] = {
    "resume": {"properties": {
        "candidate_name": _subject(v.KIND_PERSON, "candidate"),
        "email": _identifier(v.ID_EMAIL, "candidate_name"),
        "phone": _identifier(v.ID_PHONE, "candidate_name"),
        "most_recent_employer": _subject(v.KIND_ORGANIZATION, "employer",
                                         edges=[{"relation": "EMPLOYED_BY", "from": "candidate_name"}]),
    }},
    "offer_letter": {"properties": {
        "candidate_name": _subject(v.KIND_PERSON, "candidate"),
    }},
    "intake_form": {"properties": {
        "patient_name": _subject(v.KIND_PERSON, "patient"),
        "date_of_birth": _identifier(v.ID_DATE_OF_BIRTH, "patient_name"),
        "record_number": _identifier(v.ID_MEDICAL_RECORD, "patient_name"),
    }},
    "discharge_summary": {"properties": {
        "patient_name": _subject(v.KIND_PERSON, "patient"),
    }},
    "nda": {"properties": {
        "disclosing_party": _subject(v.KIND_PARTY, "disclosing_party"),
        "receiving_party": _subject(v.KIND_PARTY, "receiving_party",
                                    edges=[{"relation": "CONTRACTED_WITH", "from": "disclosing_party"}]),
    }},
    "msa": {"properties": {
        "customer": _subject(v.KIND_PARTY, "customer"),
        "supplier": _subject(v.KIND_PARTY, "supplier", edges=[{"relation": "SUPPLIES", "to": "customer"}]),
    }},
    "lease": {"properties": {
        "landlord": _subject(v.KIND_PARTY, "landlord"),
        "tenant": _subject(v.KIND_PARTY, "tenant", edges=[{"relation": "LEASES_FROM", "to": "landlord"}]),
        "premises": _subject(v.KIND_ADDRESS, "premises", edges=[
            {"relation": "OCCUPIES", "from": "tenant"}, {"relation": "LESSOR_OF", "from": "landlord"}]),
    }},
    "bill_of_lading": {"properties": {
        "bl_number": _subject(v.KIND_SHIPMENT, "shipment", identifier=v.ID_BL_NUMBER),
        "shipper": _subject(v.KIND_PARTY, "shipper", edges=[{"relation": "SHIPPED", "to": "bl_number"}]),
        "consignee": _subject(v.KIND_PARTY, "consignee", edges=[{"relation": "CONSIGNED_TO", "from": "bl_number"}]),
        "container_numbers": _subject(v.KIND_ASSET, "container", identifier=v.ID_CONTAINER_NUMBER,
                                      edges=[{"relation": "CONTAINS", "from": "bl_number"}]),
    }},
    "customs_manifest": {"properties": {
        "declaration_number": _subject(v.KIND_SHIPMENT, "declaration", identifier=v.ID_CUSTOMS_DECLARATION),
    }},
    "passport": {"properties": {
        "holder_name": _subject(v.KIND_PERSON, "holder"),
        "passport_number": _identifier(v.ID_PASSPORT, "holder_name"),
    }},
    "india_id_card": {
        "properties": {
            "holder_name": _subject(v.KIND_PERSON, "holder"),
            "date_of_birth": _identifier(v.ID_DATE_OF_BIRTH, "holder_name"),
        },
        "detect": {"of": "holder_name", "identifiers": [v.ID_AADHAAR, v.ID_PAN]},
    },
}

_CUSTOMER = _subject(v.KIND_PARTY, "customer", edges=[{"relation": "SUPPLIES", "from": "vendor_name"}])
_ACCOUNT_EDGES = [{"relation": "HOLDS_ACCOUNT", "from": "vendor_name"}]

#: The keys ENTITY_EXTRACTION_PROMPT_TEMPLATE (app/services/llm_service.py)
#: asks for, plus the spellings models commonly return for them.
BUILTIN_ANNOTATIONS: dict[str, dict[str, Any]] = {
    "vendor_name": _subject(v.KIND_ORGANIZATION, "vendor"),
    "supplier_name": _subject(v.KIND_ORGANIZATION, "vendor"),
    "vendor_gstin": _identifier(v.ID_GSTIN, "vendor_name"),
    "gstin": _identifier(v.ID_GSTIN, "vendor_name"),
    "gst_number": _identifier(v.ID_GSTIN, "vendor_name"),
    "vendor_pan": _identifier(v.ID_PAN, "vendor_name"),
    "vendor_email": _identifier(v.ID_EMAIL, "vendor_name"),
    "vendor_phone": _identifier(v.ID_PHONE, "vendor_name"),
    "vendor_address": _subject(v.KIND_ADDRESS, "vendor_address", edges=[{"relation": "LOCATED_AT", "from": "vendor_name"}]),
    "customer_name": _CUSTOMER,
    "bill_to": _CUSTOMER,
    "buyer_name": _CUSTOMER,
    "iban": _subject(v.KIND_ACCOUNT, "bank_account", identifier=v.ID_IBAN, edges=_ACCOUNT_EDGES),
    "bank_account_number": _subject(v.KIND_ACCOUNT, "bank_account", identifier=v.ID_ACCOUNT_NUMBER, edges=_ACCOUNT_EDGES),
    "candidate_name": _subject(v.KIND_PERSON, "candidate"),
    "email": _identifier(v.ID_EMAIL, "candidate_name"),
    "phone_number": _identifier(v.ID_PHONE, "candidate_name"),
    "party_names": _subject(v.KIND_PARTY, "party", pairwise="CONTRACTED_WITH"),
}


class AnnotationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_annotation(field_name: str, annotation: Any, properties: Mapping[str, Any]) -> None:
    """Refuse an `x-entity` block the resolver could not act on."""
    if not isinstance(annotation, dict):
        raise AnnotationError("ENTITY_ANNOTATION_SHAPE", f"{field_name}: x-entity must be an object.")
    unknown = set(annotation) - {"kind", "role", "identifier", "of", "edges", "pairwise"}
    if unknown:
        raise AnnotationError("ENTITY_ANNOTATION_KEYS", f"{field_name}: unknown x-entity keys {sorted(unknown)}.")
    kind, identifier = annotation.get("kind"), annotation.get("identifier")
    if kind is None and identifier is None:
        raise AnnotationError("ENTITY_ANNOTATION_EMPTY", f"{field_name}: x-entity needs a kind or an identifier.")
    if kind is not None and kind not in v.ANNOTATION_KINDS:
        raise AnnotationError("ENTITY_ANNOTATION_KIND", f"{field_name}: {kind!r} is not an entity kind.")
    if identifier is not None and identifier not in v.IDENTIFIER_KINDS:
        raise AnnotationError("ENTITY_ANNOTATION_IDENTIFIER", f"{field_name}: {identifier!r} is not an identifier kind.")
    if kind is None and annotation.get("of") not in properties:
        raise AnnotationError("ENTITY_ANNOTATION_OF", f"{field_name}: an identifier needs 'of' naming another field.")
    if kind is not None and identifier is not None and identifier not in v.IDENTIFIERS_BY_ENTITY_KIND.get(kind, ()):
        raise AnnotationError("ENTITY_ANNOTATION_IDENTIFIER", f"{field_name}: a {kind} cannot be named by {identifier}.")
    if kind is not None and not isinstance(annotation.get("role"), str):
        raise AnnotationError("ENTITY_ANNOTATION_ROLE", f"{field_name}: an entity field needs a role.")
    for edge in annotation.get("edges", []) or []:
        if not isinstance(edge, dict) or edge.get("relation") not in v.RELATIONS:
            raise AnnotationError("ENTITY_ANNOTATION_EDGE", f"{field_name}: edge relation must be one of {list(v.RELATIONS)}.")
        other = edge.get("from", edge.get("to"))
        if ("from" in edge) == ("to" in edge) or other not in properties:
            raise AnnotationError("ENTITY_ANNOTATION_EDGE", f"{field_name}: an edge names exactly one of from/to, a field of this schema.")
    if annotation.get("pairwise") is not None and annotation["pairwise"] not in v.RELATIONS:
        raise AnnotationError("ENTITY_ANNOTATION_EDGE", f"{field_name}: pairwise must be a relation.")


def validate_schema_annotations(schema: Mapping[str, Any]) -> None:
    properties = schema.get("properties") or {}
    for name, definition in properties.items():
        if isinstance(definition, dict) and ANNOTATION_KEY in definition:
            validate_annotation(name, definition[ANNOTATION_KEY], properties)
    detect = schema.get(DETECT_KEY)
    if detect is not None:
        if (not isinstance(detect, dict) or detect.get("of") not in properties
                or not set(detect.get("identifiers") or []) <= set(v.IDENTIFIER_KINDS)):
            raise AnnotationError("ENTITY_ANNOTATION_DETECT", "x-entity-detect needs 'of' (a field) and known identifiers.")


def annotations_from_schema(schema: Mapping[str, Any]) -> tuple[dict[str, dict], Optional[dict]]:
    properties = schema.get("properties") or {}
    found = {k: d[ANNOTATION_KEY] for k, d in properties.items() if isinstance(d, dict) and ANNOTATION_KEY in d}
    return found, schema.get(DETECT_KEY)


# ---------------------------------------------------------------------------
# Extraction: fields -> mention specs
# ---------------------------------------------------------------------------


@dataclass
class MentionSpec:
    key: str
    field_path: str
    ordinal: int
    kind: str
    role: str
    surface: str
    normalized: str
    source: str
    identifiers: list[tuple[str, str, bool]] = field(default_factory=list)  # (kind, value, derived)


@dataclass(frozen=True)
class EdgeSpec:
    relation: str
    src_key: str
    dst_key: str


_KEY = re.compile(r"[^0-9a-z]+")


def field_key(name: str) -> str:
    return _KEY.sub("_", str(name).strip().lower()).strip("_")


def _values(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if isinstance(x, (str, int, float)) and str(x).strip()]
    if isinstance(raw, (str, int, float)) and not isinstance(raw, bool) and str(raw).strip():
        return [str(raw).strip()]
    return []


def _add_identifier(spec: MentionSpec, kind: str, value: str) -> None:
    if kind not in v.IDENTIFIERS_BY_ENTITY_KIND.get(spec.kind, ()):
        return
    if (kind, value, False) not in spec.identifiers:
        spec.identifiers.append((kind, value, False))
    if kind == v.ID_GSTIN:
        pan = n.gstin_pan(value)
        if pan and (v.ID_PAN, pan, True) not in spec.identifiers and (v.ID_PAN, pan, False) not in spec.identifiers:
            spec.identifiers.append((v.ID_PAN, pan, True))


def extract(
    extracted: Mapping[str, Any],
    annotations: Mapping[str, Mapping[str, Any]],
    *,
    sources: Mapping[str, str],
    detect: Optional[Mapping[str, Any]] = None,
    text: str = "",
) -> tuple[list[MentionSpec], list[EdgeSpec]]:
    """Mentions and edges one document's extracted fields support."""
    values = {field_key(k): raw for k, raw in (extracted or {}).items()}
    by_field: dict[str, list[MentionSpec]] = {}

    for name, ann in annotations.items():
        kind = ann.get("kind")
        if kind is None:
            continue
        for ordinal, raw in enumerate(_values(values.get(name))):
            identifier = ann.get("identifier")
            if identifier:
                value = n.normalize_identifier(identifier, raw)
                if value is None:
                    continue
                surface = n.mask(identifier, value)
                resolved = kind
                normalized = surface.casefold()
            else:
                resolved = n.party_kind(raw) if kind == v.KIND_PARTY else kind
                surface = n.clean_display(raw)
                normalized = n.normalize_name(resolved, raw)
                if not normalized or not any(c.isalpha() for c in normalized):
                    continue
            spec = MentionSpec(
                key=f"{name}#{ordinal}", field_path=name, ordinal=ordinal, kind=resolved,
                role=str(ann.get("role") or name)[:48], surface=surface, normalized=normalized,
                source=sources.get(name, v.SOURCE_BUILTIN),
            )
            if identifier:
                _add_identifier(spec, identifier, value)
            by_field.setdefault(name, []).append(spec)

    for name, ann in annotations.items():
        identifier, of = ann.get("identifier"), ann.get("of")
        if ann.get("kind") is not None or not identifier or of not in by_field or len(by_field[of]) != 1:
            continue
        subject = by_field[of][0]
        for raw in _values(values.get(name)):
            value = n.normalize_identifier(identifier, raw)
            if value is not None:
                _add_identifier(subject, identifier, value)

    if detect and detect.get("of") in by_field and len(by_field[detect["of"]]) == 1:
        subject = by_field[detect["of"]][0]
        for kind, value in n.detect_identifiers(text, tuple(detect.get("identifiers") or ())):
            before = len(subject.identifiers)
            _add_identifier(subject, kind, value)
            if len(subject.identifiers) > before:
                subject.source = subject.source if subject.source != v.SOURCE_BUILTIN else v.SOURCE_DETECTOR

    edges: list[EdgeSpec] = []
    for name, ann in annotations.items():
        mine = by_field.get(name, [])
        for edge in ann.get("edges", []) or []:
            other = by_field.get(edge.get("from", edge.get("to")), [])
            for a in mine:
                for b in other:
                    src, dst = (b, a) if "from" in edge else (a, b)
                    if src.key != dst.key:
                        edges.append(EdgeSpec(edge["relation"], src.key, dst.key))
        if ann.get("pairwise"):
            for i, a in enumerate(mine):
                for b in mine[i + 1:]:
                    edges.append(EdgeSpec(ann["pairwise"], a.key, b.key))

    specs = [s for group in by_field.values() for s in group]
    return specs, list(dict.fromkeys(edges))


def merged_annotations(
    preset: Optional[tuple[Mapping[str, Any], Optional[Mapping[str, Any]]]],
) -> tuple[dict[str, dict], dict[str, str], Optional[dict]]:
    """Built-in annotations overlaid by the chosen preset's."""
    combined: dict[str, dict] = {k: dict(a) for k, a in BUILTIN_ANNOTATIONS.items()}
    sources = {k: v.SOURCE_BUILTIN for k in combined}
    detect = None
    if preset is not None:
        fields, detect = preset
        for key, ann in fields.items():
            combined[key] = dict(ann)
            sources[key] = v.SOURCE_PRESET
    return combined, sources, (dict(detect) if detect else None)
