"""ARCH43-S1:doc-types — one document type per work item.

In order of trust: an ARCH-31 role a person set or the classifier stored
(document_roles, not OTHER); the enrichment's `document_classification`
mapped onto the preset keys; the ARCH-43 page classifier over the first
pages. Types are lowercase keys (invoice, purchase_order, resume,
bill_of_lading, ...), the same keys case templates require.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.packets import page_classifier as pc

SYNONYMS = {
    "tax_invoice": "invoice", "commercial_invoice": "invoice", "bill": "invoice", "po": "purchase_order",
    "grn": "goods_receipt", "goods_receipt_note": "goods_receipt", "delivery_challan": "goods_receipt",
    "delivery_note": "goods_receipt", "cv": "resume", "curriculum_vitae": "resume", "b_l": "bill_of_lading",
    "bol": "bill_of_lading", "aadhaar": "india_id_card", "pan_card": "india_id_card", "statement_of_account": "statement",
    "contract": "contract", "master_services_agreement": "msa", "non_disclosure_agreement": "nda",
}


def normalise(label: Any) -> Optional[str]:
    if not label:
        return None
    key = re.sub(r"[^a-z0-9]+", "_", str(label).strip().lower()).strip("_")
    if not key or key in ("other", "unknown", "none"):
        return None
    return SYNONYMS.get(key, key)[:64]


def document_type_of(db: Session, work_item: Any) -> Optional[str]:
    try:
        role = db.execute(text("SELECT role FROM document_roles WHERE work_item_id = :w"), {"w": work_item.id}).scalar()
    except Exception:  # noqa: BLE001 - no roles table on an old schema is "no role"
        role = None
    if role and role != "OTHER":
        return normalise(role)
    fields = work_item.extracted_entities if isinstance(work_item.extracted_entities, dict) else {}
    classified = normalise(fields.get("document_classification") or fields.get("document_type"))
    if classified:
        return classified
    first = "\n".join((work_item.extracted_text or "").splitlines()[:80])
    verdict = pc.classify_page(first)
    return None if verdict.doc_type == pc.OTHER else verdict.doc_type
