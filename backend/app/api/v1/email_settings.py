from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.schemas.email_settings import (
    EmailSettingsCreate,
    EmailSettingsResponse,
    TestEmailRequest,
    TestEmailResponse,
)
from app.services.email_service import email_service

logger = logging.getLogger(
    "app.api.v1.email_settings"
)

router = APIRouter(
    tags=["Email Settings"],
)


# ============================================================================
# Get
# ============================================================================


@router.get(
    "",
    response_model=EmailSettingsResponse,
    summary="Get Email Settings",
)
async def get_email_settings(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor)
) -> EmailSettingsResponse:
    """
    Returns the authenticated user's SMTP settings.
    """

    settings = crud.get_email_settings(
        db,
        user_id=context.user_id,
    )

    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email settings not configured.",
        )

    return settings


# ============================================================================
# Create / Update
# ============================================================================


@router.put(
    "",
    response_model=EmailSettingsResponse,
    summary="Create or Update Email Settings",
)
async def upsert_email_settings(
    settings_in: EmailSettingsCreate,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin)
) -> EmailSettingsResponse:
    """
    Creates or updates SMTP settings.
    """

    settings = crud.upsert_email_settings(
        db,
        user_id=context.user_id,
        settings_in=settings_in,
    )

    logger.info(
        "Updated email settings for user %s.",
        context.user_id,
    )

    return settings


# ============================================================================
# Test SMTP
# ============================================================================


@router.post(
    "/test",
    response_model=TestEmailResponse,
    summary="Send Test Email",
)
async def test_email_settings(
    request: TestEmailRequest,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin)
) -> TestEmailResponse:
    """
    Sends a test email using the user's SMTP configuration.
    """

    settings = crud.get_email_settings(
        db,
        user_id=context.user_id,
    )

    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email settings not configured.",
        )

    success, message = email_service.send_email(
        settings=settings,
        recipient=request.recipient,
        subject="FlowPilot AI SMTP Test",
        body=(
            "Congratulations!\n\n"
            "Your SMTP configuration is working correctly."
        ),
    )

    return TestEmailResponse(
        success=success,
        message=message,
    )