"""ARCH-14 Step 8 CONTRACT: AI Settings endpoints without cost parameters."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.core.ai_models import AI_MODELS, requires_own_key
from app.core.exceptions import ServaboError
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
def get_ai_settings(
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
def upsert_ai_settings(
    settings_in: AISettingsUpdate,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
) -> AISettingsResponse:
    _assert_model_available(db, organization_id=context.organization_id, settings_in=settings_in)
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


class OwnKeyRequiredError(ServaboError):
    """The model runs on the organization's own provider key, which it has not added (N-054)."""

    status_code = 422
    code = "OWN_KEY_REQUIRED"

    def __init__(self, message: str, *, details: dict) -> None:
        super().__init__(message)
        self.details = details


_PROVIDER_LABELS = {"GROQ": "Groq", "GEMINI": "Google Gemini"}


def _own_key_ready(db: Session, *, organization_id, provider: str) -> bool:
    from app.services.byok import credential_service

    credential = credential_service.resolve_active(db, organization_id=organization_id, provider=provider)
    return credential is not None and not credential.validation_error


def _assert_model_available(db: Session, *, organization_id, settings_in: AISettingsUpdate) -> None:
    """Campaign session 2 (N-054). Only listed models; a premium one only with the organization's own key."""
    provider = settings_in.provider.value
    listed = AI_MODELS.get(settings_in.provider, [])
    if settings_in.model not in listed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"{settings_in.model} is not a model Servabo offers on "
                f"{_PROVIDER_LABELS.get(provider, provider)}. Choose one of: {', '.join(listed)}."
            ),
        )
    if requires_own_key(provider, settings_in.model) and not _own_key_ready(
        db, organization_id=organization_id, provider=provider
    ):
        label = _PROVIDER_LABELS.get(provider, provider)
        raise OwnKeyRequiredError(
            f"{settings_in.model} runs on your organization's own {label} key, which has not been added. "
            f"An organization admin can add one under Organization settings, AI provider keys; until then "
            f"choose GPT-OSS 20B or GPT-OSS 120B, which Servabo runs within your plan.",
            details={"provider": provider, "model": settings_in.model, "remedy": "ADD_PROVIDER_KEY"},
        )


@router.get(
    "/models/availability",
    summary="Which models this workspace can run now, and on whose key",
)
def get_model_availability(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> dict[str, dict[str, str]]:
    """PLATFORM (inside the plan), OWN_KEY_READY (the organization's key is added) or OWN_KEY_NEEDED."""
    out: dict[str, dict[str, str]] = {}
    for provider, models in AI_MODELS.items():
        ready = _own_key_ready(db, organization_id=context.organization_id, provider=provider.value)
        out[provider.value] = {
            model: ("PLATFORM" if not requires_own_key(provider, model) else
                    "OWN_KEY_READY" if ready else "OWN_KEY_NEEDED")
            for model in models
        }
    return out


@router.get(
    "/resolved",
    response_model=ResolvedAISettingsResponse,
    summary="What actually serves this workspace, and what it costs",
    response_description=(
        "Read-only. The routed provider and model, where that decision came "
        "from, the price-book rate in force, BYOK and spend-limit state."
    ),
)
def get_resolved_ai_settings(
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
def get_supported_models(
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
def get_available_providers(
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
