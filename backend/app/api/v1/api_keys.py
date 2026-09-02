"""
API Key management router for FlowPilot AI (ARCH-08 §B.1, §B.2, §B.12, §9.6).

    POST   /organizations/{organization_id}/api-keys
    GET    /organizations/{organization_id}/api-keys
    GET    /organizations/{organization_id}/api-keys/{key_id}
    POST   /organizations/{organization_id}/api-keys/{key_id}/rotate
    DELETE /organizations/{organization_id}/api-keys/{key_id}
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api import deps
from app.core.exceptions import OrganizationPermissionDeniedError, WorkspacePermissionDeniedError
from app.crud import api_key as api_key_crud
from app.models.audit_log import AuditAction, AuditResourceType
from app.schemas.api_key import (
    ApiKeyCreate,
    ApiKeyRead,
    ApiKeyResponse,
    ApiKeyRotateRequest,
)
from app.services import api_key_service, audit_service

logger = logging.getLogger("app.api.v1.api_keys")

router = APIRouter(tags=["API Keys"])


def _assert_human_admin(request: Request, context: deps.OrganizationContext) -> None:
    principal = getattr(request.state, "principal", None) or deps.get_current_principal()
    if (principal and principal.kind == "API_KEY") or getattr(request.state, "api_key_id", None) is not None:
        raise OrganizationPermissionDeniedError(
            "API key management requires a human administrator session."
        )


@router.post(
    "/organizations/{organization_id}/api-keys",
    response_model=ApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue API Key",
)
def create_api_key(
    payload: ApiKeyCreate,
    db: deps.DbSession,
    request: Request,
    context: deps.OrganizationContext = Depends(deps.RequireOrgAdmin),
) -> Any:
    _assert_human_admin(request, context)

    key, token = api_key_service.issue_api_key(
        db,
        organization_id=context.organization_id,
        actor=context.membership,
        name=payload.name,
        scopes=[s.value for s in payload.scopes],
        expires_at=payload.expires_at,
    )
    db.commit()
    db.refresh(key)

    return ApiKeyResponse(
        api_key=ApiKeyRead.model_validate(key),
        token=token,
    )


@router.get(
    "/organizations/{organization_id}/api-keys",
    response_model=list[ApiKeyRead],
    summary="List API Keys",
)
def list_api_keys(
    db: deps.DbSession,
    request: Request,
    context: deps.OrganizationContext = Depends(deps.RequireOrgAdmin),
) -> Any:
    _assert_human_admin(request, context)
    keys = api_key_crud.list_api_keys_for_organization(
        db, organization_id=context.organization_id, include_deactivated=True
    )
    return [ApiKeyRead.model_validate(k) for k in keys]


@router.get(
    "/organizations/{organization_id}/api-keys/{key_id}",
    response_model=ApiKeyRead,
    summary="Get API Key Detail",
)
def get_api_key(
    key_id: UUID,
    db: deps.DbSession,
    request: Request,
    context: deps.OrganizationContext = Depends(deps.RequireOrgAdmin),
) -> Any:
    _assert_human_admin(request, context)
    key = api_key_crud.get_api_key_by_id(
        db, organization_id=context.organization_id, key_id=key_id
    )
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key not found."
        )
    return ApiKeyRead.model_validate(key)


@router.post(
    "/organizations/{organization_id}/api-keys/{key_id}/rotate",
    response_model=ApiKeyResponse,
    summary="Rotate API Key",
)
def rotate_api_key(
    key_id: UUID,
    payload: ApiKeyRotateRequest,
    db: deps.DbSession,
    request: Request,
    context: deps.OrganizationContext = Depends(deps.RequireOrgAdmin),
) -> Any:
    _assert_human_admin(request, context)
    try:
        key, new_token = api_key_service.rotate_api_key(
            db,
            organization_id=context.organization_id,
            key_id=key_id,
            actor=context.membership,
            force=payload.force,
        )
        db.commit()
        db.refresh(key)
        return ApiKeyResponse(
            api_key=ApiKeyRead.model_validate(key),
            token=new_token,
        )
    except WorkspacePermissionDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.delete(
    "/organizations/{organization_id}/api-keys/{key_id}",
    response_model=ApiKeyRead,
    summary="Revoke API Key",
)
def revoke_api_key(
    key_id: UUID,
    db: deps.DbSession,
    request: Request,
    context: deps.OrganizationContext = Depends(deps.RequireOrgAdmin),
) -> Any:
    _assert_human_admin(request, context)
    key = api_key_crud.get_api_key_by_id(
        db, organization_id=context.organization_id, key_id=key_id
    )
    if key is None or key.deactivated_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key not found or already revoked."
        )

    deactivated = api_key_crud.deactivate_api_key(db, key=key, reason="MANUAL")

    audit_service.record(
        db,
        organization_id=context.organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.API_KEY,
        resource_id=key.id,
        action=AuditAction.REVOKED,
        details={"reason": "MANUAL", "key_name": key.name},
    )
    db.commit()
    db.refresh(deactivated)

    return ApiKeyRead.model_validate(deactivated)
