from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import deps
from app.crud import (
    get_document_settings,
    upsert_document_settings,
)
from app.models.user import User
from app.models.workspace import WorkspaceRole
from app.schemas.document_settings import (
    DocumentSettingsCreate,
    DocumentSettingsResponse,
)

router = APIRouter(tags=["Document Settings"])


@router.get(
    "/",
    response_model=DocumentSettingsResponse,
    dependencies=[Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER, WorkspaceRole.CONTRIBUTOR]))]
)
async def get_document_processing_settings(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:

    settings = get_document_settings(
        db=db,
        user_id=current_user.id,
    )

    if settings is None:
        settings = upsert_document_settings(
            db=db,
            user_id=current_user.id,
            settings_in=DocumentSettingsCreate(),
        )

    return settings


@router.put(
    "/",
    response_model=DocumentSettingsResponse,
    dependencies=[Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER]))]
)
async def update_document_processing_settings(
    settings_in: DocumentSettingsCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:

    return upsert_document_settings(
        db=db,
        user_id=current_user.id,
        settings_in=settings_in,
    )