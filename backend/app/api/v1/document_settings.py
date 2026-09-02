from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import deps
from app import crud
from app.schemas.document_settings import (
    DocumentSettingsCreate,
    DocumentSettingsResponse,
)

router = APIRouter(tags=["Document Settings"])


@router.get(
    "/",
    response_model=DocumentSettingsResponse,
)
async def get_document_processing_settings(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor)
) -> Any:
    settings = crud.get_document_settings(
        db=db,
        workspace_id=context.workspace_id,
    )

    if settings is None:
        settings = crud.upsert_document_settings(
            db=db,
            workspace_id=context.workspace_id,
            updated_by_user_id=context.user_id,
            settings_in=DocumentSettingsCreate(),
        )

    return settings


@router.put(
    "/",
    response_model=DocumentSettingsResponse,
)
async def update_document_processing_settings(
    settings_in: DocumentSettingsCreate,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin)
) -> Any:
    return crud.upsert_document_settings(
        db=db,
        workspace_id=context.workspace_id,
        updated_by_user_id=context.user_id,
        settings_in=settings_in,
    )
