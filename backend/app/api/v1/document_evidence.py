"""PHASE 4 — page images and value locations for one document.

    GET /workspaces/{wid}/work-items/{id}/evidence          where values sit   [VIEWER]
    GET /workspaces/{wid}/work-items/{id}/pages/{n}.png     one page, rendered [VIEWER]

The review hub draws the second with the first's boxes on top, so a reviewer
deciding between two readings of a field sees where each one is printed.
`evidence` also accepts `candidate=<field>:<value>` (repeatable) for values
that are not the document's current reading - what each verification agent
answered, for instance.
"""

from __future__ import annotations

import io
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.core.pdfium_lock import PDFIUM_LOCK, pdfium_page
from app.services import document_evidence_service

router = APIRouter(tags=["Work Items"])

RENDERABLE = ("application/pdf", "image/png", "image/jpeg", "image/tiff", "image/webp")


class PageOut(BaseModel):
    page_number: int
    width: Optional[float] = None
    height: Optional[float] = None


class LocationOut(BaseModel):
    field: Optional[str] = None
    value: str
    source: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


class MissingOut(BaseModel):
    field: Optional[str] = None
    value: str
    source: str


class EvidenceOut(BaseModel):
    work_item_id: uuid.UUID
    renderable: bool
    pages: list[PageOut]
    locations: list[LocationOut]
    not_found: list[MissingOut]


def _item(db: Session, context: deps.TenantContext, work_item_id: uuid.UUID):
    item = crud.get_work_item(db, workspace_id=context.workspace_id, work_item_id=work_item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Work item not found.")
    return item


def _mime(item) -> str:
    return (item.file_type or "").split(";")[0].strip().lower()


@router.get("/{work_item_id}/evidence", response_model=EvidenceOut, summary="Where each value is printed")
def document_evidence(
    work_item_id: uuid.UUID,
    candidate: list[str] = Query(default_factory=list, max_length=40),
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> EvidenceOut:
    item = _item(db, context, work_item_id)
    pairs: list[tuple[Optional[str], str]] = []
    for raw in candidate:
        key, sep, value = raw.partition(":")
        pairs.append((key.strip() or None, value) if sep else (None, raw))
    evidence = document_evidence_service.locate(
        item.extraction_metadata, item.extracted_entities, candidates=pairs
    )
    return EvidenceOut(
        work_item_id=item.id,
        renderable=_mime(item) in RENDERABLE,
        pages=[PageOut(**p) for p in evidence.pages],
        locations=[LocationOut(**vars(loc)) for loc in evidence.locations],
        not_found=[MissingOut(**m) for m in evidence.not_found],
    )


@router.get("/{work_item_id}/pages/{page}.png", summary="One page of the document as an image")
def document_page_image(
    work_item_id: uuid.UUID,
    page: int,
    dpi: int = Query(default=100, ge=50, le=200),
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> Response:
    item = _item(db, context, work_item_id)
    mime = _mime(item)
    if mime not in RENDERABLE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {"code": "NOT_RENDERABLE", "message": "This document has no page images."})
    from app.core.storage import get_storage_driver

    raw = get_storage_driver().get(item.stored_filename)
    buffer = io.BytesIO()
    if mime == "application/pdf":
        import pypdfium2 as pdfium

        # F-066: PDFium is not thread-safe; hold the process lock for the document's lifetime.
        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(raw)
            try:
                if not 1 <= page <= len(pdf):
                    raise HTTPException(status.HTTP_404_NOT_FOUND, {"code": "NO_PAGE", "message": "Page out of range."})
                with pdfium_page(pdf, page - 1) as pdf_page:
                    pdf_page.render(scale=dpi / 72.0).to_pil().save(buffer, format="PNG", optimize=True)
            finally:
                pdf.close()
    else:
        from PIL import Image

        if page != 1:
            raise HTTPException(status.HTTP_404_NOT_FOUND, {"code": "NO_PAGE", "message": "Page out of range."})
        with Image.open(io.BytesIO(raw)) as image:
            image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return Response(content=buffer.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "private, max-age=300"})
