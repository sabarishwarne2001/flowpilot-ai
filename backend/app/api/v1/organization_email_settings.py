"""
Per-organization SMTP configuration routes.

ARCH-06 Step 8, §B.5 Option B.

    GET    /organizations/{organization_id}/email-settings
    PATCH  /organizations/{organization_id}/email-settings
    POST   /organizations/{organization_id}/email-settings/test
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api import deps
from app.schemas.organization_email_settings import (
    OrganizationEmailSettingsResponse,
    OrganizationEmailSettingsUpdate,
    OrganizationEmailTestRequest,
    OrganizationEmailTestResponse,
)
from app.services import organization_email_settings_service as org_smtp
from app.services.email_service import email_service

logger = logging.getLogger("app.api.v1.organization_email_settings")

router = APIRouter(tags=["Organization Email Settings"])


def _to_response(row) -> OrganizationEmailSettingsResponse:
    """
    Serializes a row, deriving the two computed fields.
    """
    return OrganizationEmailSettingsResponse(
        id=row.id,
        organization_id=row.organization_id,
        smtp_host=row.smtp_host,
        smtp_port=row.smtp_port,
        smtp_username=row.smtp_username,
        sender_name=row.sender_name,
        sender_email=row.sender_email,
        encryption=row.encryption,
        is_enabled=row.is_enabled,
        has_password=row.encrypted_password is not None,
        is_complete=row.is_complete,
        updated_by_user_id=row.updated_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/organizations/{organization_id}/email-settings",
    response_model=OrganizationEmailSettingsResponse,
    summary="Get organization SMTP configuration",
)
async def read_organization_email_settings(
    organization_id: uuid.UUID,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    row = org_smtp.get_or_create_settings(
        db, organization_id=context.organization_id
    )
    return _to_response(row)


@router.patch(
    "/organizations/{organization_id}/email-settings",
    response_model=OrganizationEmailSettingsResponse,
    summary="Update organization SMTP configuration",
)
async def update_organization_email_settings(
    organization_id: uuid.UUID,
    payload: OrganizationEmailSettingsUpdate,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    try:
        row = org_smtp.set_settings(
            db,
            organization_id=context.organization_id,
            payload=payload,
            actor=context.user,
        )
    except org_smtp.IncompleteConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )

    return _to_response(row)


@router.post(
    "/organizations/{organization_id}/email-settings/test",
    response_model=OrganizationEmailTestResponse,
    summary="Test organization SMTP configuration",
)
async def test_organization_email_settings(
    organization_id: uuid.UUID,
    payload: OrganizationEmailTestRequest,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    row = org_smtp.get_settings(db, organization_id=context.organization_id)

    if row is None or not row.is_complete:
        return OrganizationEmailTestResponse(
            success=False,
            message=(
                "This organization's SMTP configuration is incomplete. "
                "Provide a host, port, username, password, sender name, and "
                "encryption before testing."
            ),
        )

    try:
        config = org_smtp.to_smtp_config(row)
    except Exception:
        logger.exception(
            "ORG_SMTP_DECRYPT_FAILED | organization=%s", context.organization_id
        )
        return OrganizationEmailTestResponse(
            success=False,
            message=(
                "The stored password could not be decrypted. Re-enter it and "
                "save before testing again."
            ),
        )

    success, message = email_service.test_connection(config)

    logger.info(
        "ORG_SMTP_TEST | organization=%s | actor=%s | success=%s",
        context.organization_id,
        context.user_id,
        success,
    )

    if not success:
        return OrganizationEmailTestResponse(success=False, message=message)

    delivered, detail = email_service.send_email(
        settings=config,
        recipient=payload.recipient,
        subject="FlowPilot SMTP test",
        body=(
            "This is a test message confirming your organization's SMTP "
            "configuration is working.\n\nIf you did not request this, you "
            "can ignore it."
        ),
    )
    return OrganizationEmailTestResponse(
        success=delivered,
        message=detail if not delivered else "Test email sent successfully.",
    )
