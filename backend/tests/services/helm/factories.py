"""Documents as the pipeline leaves them, for Helm's tests: a work item with extracted fields and the
`document_roles` row post-enrichment writes (role, vendor key, number, date, currency, total)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core import normalize as nz
from app.models.document_role import DocumentRole
from app.models.work_item import WorkItem
from app.services.helm import money


def document(db: Session, *, workspace, organization_id: uuid.UUID, role: str, entities: dict[str, Any],
             number: Optional[str] = None, on: Optional[date] = None, currency: str = "INR",
             total: Optional[float] = None, vendor: Optional[str] = None, vendor_tax: Optional[str] = None,
             text: str = "", filename: Optional[str] = None, created_at: Optional[datetime] = None) -> WorkItem:
    item = WorkItem(
        workspace_id=workspace.id, original_filename=filename or f"{role.lower()}-{number or uuid.uuid4().hex[:6]}.pdf",
        stored_filename=f"helm-test-{uuid.uuid4().hex}.pdf", file_type="application/pdf", file_size=2048,
        status="COMPLETED", pipeline_stage="COMPLETED", extracted_entities=dict(entities), extracted_text=text,
        extraction_metadata={},
    )
    if created_at is not None:
        item.created_at = created_at
    db.add(item)
    db.flush()
    vendor_key = None
    if vendor or vendor_tax:
        try:
            vendor_key = nz.vendor_key(vendor, vendor_tax)
        except nz.NormalizationError:
            vendor_key = None
    db.add(DocumentRole(
        organization_id=organization_id, workspace_id=workspace.id, work_item_id=item.id, role=role,
        role_source="CLASSIFIER", role_confidence=0.9, vendor_key=vendor_key, document_number=number,
        document_date=on, currency=currency, total_micros=money.to_micros(total) if total is not None else None,
    ))
    db.flush()
    return item


def invoice(db: Session, *, workspace, organization_id: uuid.UUID, number: str, supplier: str, amount: float,
            on: date, terms: Optional[str] = "Net 30", due: Optional[date] = None, buyer: str = "Meridian Wholesale",
            currency: str = "INR", supplier_gstin: Optional[str] = None, bank: Optional[str] = None,
            po: Optional[str] = None, extra: Optional[dict[str, Any]] = None, text: str = "") -> WorkItem:
    entities: dict[str, Any] = {"document_classification": "Invoice", "vendor_name": supplier,
                                "invoice_number": number, "invoice_date": on.isoformat(), "bill_to": buyer,
                                "total_amount": f"{amount:.2f}", "currency": currency}
    if terms:
        entities["payment_terms"] = terms
    if due:
        entities["due_date"] = due.isoformat()
    if supplier_gstin:
        entities["supplier_gstin"] = supplier_gstin
    if bank:
        entities["vendor_bank_account"] = bank
    if po:
        entities["po_number"] = po
    entities.update(extra or {})
    return document(db, workspace=workspace, organization_id=organization_id, role="INVOICE", entities=entities,
                    number=number, on=on, currency=currency, total=amount, vendor=supplier, vendor_tax=supplier_gstin,
                    text=text or f"TAX INVOICE {number} {supplier}")


__all__ = ["document", "invoice"]
