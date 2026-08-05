from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.models.workspace import WorkspaceMember, WorkspaceRole
from app.services.ai_settings_service import (
    ai_settings_service,
)
from app.schemas.ai_settings import (
    AISettingsResponse,
    AISettingsUpdate,
)
from app.schemas.available_providers import (
    AvailableProvidersResponse,
)
from app.schemas.ai_connection_test import (
    AIConnectionTestResponse,
)
from app.core.ai_models import AI_MODELS

logger = logging.getLogger(
    "app.api.v1.ai_settings"
)

router = APIRouter(
    tags=["AI Settings"],
)


# ============================================================================
# Get
# ============================================================================


@router.get(
    "",
    response_model=AISettingsResponse,
    summary="Get AI Settings",
)
async def get_ai_settings(
    db: Session = Depends(deps.get_db),
    membership: WorkspaceMember = Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER, WorkspaceRole.CONTRIBUTOR]))
) -> AISettingsResponse:
    """
    Returns the authenticated user's AI settings.
    """

    settings = crud.get_ai_settings(
        db,
        user_id=membership.user_id,
    )

    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI settings not configured.",
        )

    return settings


# ============================================================================
# Update
# ============================================================================


@router.put(
    "",
    response_model=AISettingsResponse,
    summary="Create or Update AI Settings",
)
async def upsert_ai_settings(
    settings_in: AISettingsUpdate,
    db: Session = Depends(deps.get_db),
    membership: WorkspaceMember = Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER]))
) -> AISettingsResponse:
    """
    Creates or updates the authenticated user's AI settings.
    """

    settings = crud.upsert_ai_settings(
        db,
        user_id=membership.user_id,
        settings_in=settings_in,
    )

    logger.info(
        "Updated AI settings for user %s.",
        membership.user_id,
    )

    return settings


@router.get(
    "/models",
    summary="Get Supported AI Models",
)
async def get_supported_models(
    membership: WorkspaceMember = Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER, WorkspaceRole.CONTRIBUTOR]))
):
    """
    Returns the supported models for every AI provider.
    """

    return {
        provider.value: models
        for provider, models in AI_MODELS.items()
    }


@router.get(
    "/providers",
    response_model=AvailableProvidersResponse,
    summary="Get Available AI Providers",
)
async def get_available_providers(
    membership: WorkspaceMember = Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER, WorkspaceRole.CONTRIBUTOR]))
):
    """
    Returns only providers that are configured.
    """

    return ai_settings_service.get_available_providers()


@router.post(
    "/test",
    response_model=AIConnectionTestResponse,
    summary="Test AI Configuration",
)
async def test_ai_configuration(
    settings_in: AISettingsUpdate,
    membership: WorkspaceMember = Depends(deps.RequireRole([WorkspaceRole.OWNER, WorkspaceRole.MANAGER]))
):
    """
    Test the supplied AI configuration without
    saving it to the database.
    """

    return ai_settings_service.test_connection(
        ai_settings=settings_in,
    )