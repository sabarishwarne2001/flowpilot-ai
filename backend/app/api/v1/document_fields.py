"""N-020 items 6 and 7 — the document viewer: its text, and correcting its extracted fields.

    GET   /workspaces/{wid}/work-items/{id}/text             the extracted text        [VIEWER]
    GET   /workspaces/{wid}/work-items/{id}/fields           may the caller correct?   [VIEWER]
    PATCH /workspaces/{wid}/work-items/{id}/fields           correct extracted fields  [CONTRIBUTOR]
    GET   /workspaces/{wid}/work-items/{id}/fields/history   who corrected what        [VIEWER]

The page images and value locations the viewer draws come from
`document_evidence` (Phase 4); the rules a correction obeys are in
`app/services/field_correction_service.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.models.workspace import WorkspaceRole
from app.services import field_correction_service

router = APIRouter(tags=["Work Items"])

#: The viewer shows the text, not a download; a 300-page contract stays readable.
MAX_TEXT_CHARS = 200_000


class DocumentTextOut(BaseModel):
    work_item_id: uuid.UUID
    text: str
    characters: int
    truncated: bool


class FieldCorrectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corrections: dict[str, Any] = Field(
        ..., description="Field name -> corrected value (text, number, true/false or null)."
    )
    reason: Optional[str] = Field(default=None, max_length=500)


class FieldCorrectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    field_path: str
    previous_value: Optional[Any] = None
    corrected_value: Optional[Any] = None
    corrected_by_user_id: Optional[uuid.UUID] = None
    reason: Optional[str] = None
    created_at: datetime


class FieldEditabilityOut(BaseModel):
    work_item_id: uuid.UUID
    editable: bool
    code: Optional[str] = None
    message: Optional[str] = None


class FieldCorrectionResult(BaseModel):
    work_item_id: uuid.UUID
    extracted_entities: dict[str, Any]
    corrected: list[FieldCorrectionOut]
    extraction_memory_fields: list[str]


def _item(db: Session, context: deps.TenantContext, work_item_id: uuid.UUID):
    item = crud.get_work_item(db, workspace_id=context.workspace_id, work_item_id=work_item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Work item not found.")
    return item


@router.get("/{work_item_id}/text", response_model=DocumentTextOut, summary="The document's extracted text")
def document_text(
    work_item_id: uuid.UUID,
    db: Session = Depends(deps.get_read_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> DocumentTextOut:
    item = _item(db, context, work_item_id)
    text = item.extracted_text or ""
    return DocumentTextOut(
        work_item_id=item.id,
        text=text[:MAX_TEXT_CHARS],
        characters=len(text),
        truncated=len(text) > MAX_TEXT_CHARS,
    )


@router.get(
    "/{work_item_id}/fields",
    response_model=FieldEditabilityOut,
    summary="Whether the caller may correct this document's fields now, and if not why",
)
def field_editability(
    work_item_id: uuid.UUID,
    db: Session = Depends(deps.get_read_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> FieldEditabilityOut:
    item = _item(db, context, work_item_id)
    if context.role is WorkspaceRole.VIEWER:
        return FieldEditabilityOut(
            work_item_id=item.id, editable=False, code="READ_ONLY_ROLE",
            message="You have view-only access to this workspace. Ask a workspace administrator to correct a field.",
        )
    blocked = field_correction_service.editability(db, work_item=item, organization_id=context.organization_id)
    if blocked is not None:
        return FieldEditabilityOut(work_item_id=item.id, editable=False, code=blocked.code, message=blocked.message)
    return FieldEditabilityOut(work_item_id=item.id, editable=True)


@router.patch(
    "/{work_item_id}/fields",
    response_model=FieldCorrectionResult,
    summary="Correct extracted fields from the document viewer",
)
def correct_fields(
    work_item_id: uuid.UUID,
    payload: FieldCorrectionIn,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> FieldCorrectionResult:
    item = _item(db, context, work_item_id)
    try:
        outcome = field_correction_service.correct_fields(
            db,
            work_item=item,
            organization_id=context.organization_id,
            user_id=context.user_id,
            corrections=payload.corrections,
            reason=payload.reason,
        )
    except field_correction_service.FieldCorrectionError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc
    db.commit()
    db.refresh(item)
    return FieldCorrectionResult(
        work_item_id=item.id,
        extracted_entities=dict(item.extracted_entities or {}),
        corrected=[FieldCorrectionOut.model_validate(row) for row in outcome.changed],
        extraction_memory_fields=outcome.memory_fields,
    )


@router.get(
    "/{work_item_id}/fields/history",
    response_model=list[FieldCorrectionOut],
    summary="Corrections made to this document's extracted fields, newest first",
)
def field_history(
    work_item_id: uuid.UUID,
    db: Session = Depends(deps.get_read_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> list[FieldCorrectionOut]:
    item = _item(db, context, work_item_id)
    return [FieldCorrectionOut.model_validate(row) for row in field_correction_service.history(db, work_item=item)]


__all__ = ["router"]
