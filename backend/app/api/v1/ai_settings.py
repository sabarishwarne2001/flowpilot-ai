from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.core.ai_models import AI_MODELS
from app.schemas.ai_connection_test import AIConnectionTestResponse
from app.schemas.ai_settings import AISettingsResponse, AISettingsUpdate
from app.schemas.available_providers import AvailableProvidersResponse
from app.services.ai_settings_service import ai_settings_service

logger = logging.getLogger("app.api.v1.ai_settings")

router = APIRouter(
    tags=["AI Settings"],
)


@router.get(
    "",
    response_model=AISettingsResponse,
    summary="Get AI Settings",
)
async def get_ai_settings(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> AISettingsResponse:
    settings = crud.get_ai_settings(
        db,
        workspace_id=context.workspace_id,
    )

    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI settings not configured.",
        )

    return settings


@router.put(
    "",
    response_model=AISettingsResponse,
    summary="Create or Update AI Settings",
)
async def upsert_ai_settings(
    settings_in: AISettingsUpdate,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
) -> AISettingsResponse:
    settings = crud.upsert_ai_settings(
        db,
        workspace_id=context.workspace_id,
        updated_by_user_id=context.user_id,
        settings_in=settings_in,
    )

    logger.info(
        "Updated AI settings inside workspace %s by user %s.",
        context.workspace_id,
        context.user_id,
    )

    return settings


@router.get(
    "/models",
    summary="Get Supported AI Models",
)
async def get_supported_models(
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
):
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
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
):
    return ai_settings_service.get_available_providers()


@router.post(
    "/test",
    response_model=AIConnectionTestResponse,
    summary="Test AI Configuration",
)
async def test_ai_configuration(
    settings_in: AISettingsUpdate,
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
):
    return ai_settings_service.test_connection(
        ai_settings=settings_in,
    )