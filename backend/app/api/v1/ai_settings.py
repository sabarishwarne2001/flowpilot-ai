"""ARCH-14 Step 8 CONTRACT: AI Settings endpoints without cost parameters."""

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
from app.schemas.ai_settings_resolved import ResolvedAISettingsResponse
from app.services import ai_settings_resolution
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
    settings_obj = crud.get_ai_settings(
        db,
        workspace_id=context.workspace_id,
    )

    if settings_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI settings not configured.",
        )

    return settings_obj


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
    settings_obj = crud.upsert_ai_settings(
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

    return settings_obj


@router.get(
    "/resolved",
    response_model=ResolvedAISettingsResponse,
    summary="What actually serves this workspace, and what it costs",
    response_description=(
        "Read-only. The routed provider and model, where that decision came "
        "from, the price-book rate in force, BYOK and spend-limit state."
    ),
)
async def get_resolved_ai_settings(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> ResolvedAISettingsResponse:
    """ARCH40-S1:ai-settings-resolved.

    A workspace administrator could set `model` and have no way to learn that
    an organization routing rule sent extraction somewhere else — the console
    showed the value that lost. This endpoint shows the one that won, names
    the owner, and says so in `warnings` when the two differ.

    Nothing here is writable. `tenant_model_routes` owns routing for a routed
    task and `ai_settings` owns the workspace default; that layering is
    ARCH-22's and ARCH-40 does not change it. What changes is that the console
    stops implying the loser is in force.
    """
    settings_obj = crud.get_ai_settings(db, workspace_id=context.workspace_id)
    if settings_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI settings not configured.",
        )

    resolved = ai_settings_resolution.resolve(
        db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        ai_settings=settings_obj,
    )
    return ResolvedAISettingsResponse(**resolved.as_dict())


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
def test_ai_configuration(
    settings_in: AISettingsUpdate,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
):
    # HARDENING-T1:D2. A plain `def`: the provider SDKs are synchronous, and
    # an `async def` here held the event loop for the whole round trip.
    return ai_settings_service.test_connection(
        db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        ai_settings=settings_in,
    )
