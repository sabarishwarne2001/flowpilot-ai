from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.models.user import User
from app.schemas.ai_settings import (
    AISettingsResponse,
    AISettingsUpdate,
)

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
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
) -> AISettingsResponse:
    """
    Returns the authenticated user's AI settings.
    """

    settings = crud.get_ai_settings(
        db,
        user_id=current_user.id,
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
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
) -> AISettingsResponse:
    """
    Creates or updates the authenticated user's AI settings.
    """

    settings = crud.upsert_ai_settings(
        db,
        user_id=current_user.id,
        settings_in=settings_in,
    )

    logger.info(
        "Updated AI settings for user %s.",
        current_user.id,
    )

    return settings