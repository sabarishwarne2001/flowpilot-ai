from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import deps
from app.crud import (
    get_document_settings,
    upsert_document_settings,
)
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

    settings = get_document_settings(
        db=db,
        user_id=context.user_id,
    )

    if settings is None:
        settings = upsert_document_settings(
            db=db,
            user_id=context.user_id,
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

    return upsert_document_settings(
        db=db,
        user_id=context.user_id,
        settings_in=settings_in,
    )