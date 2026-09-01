"""ARCH-21 §3.4 — the tenant developer portal.

    GET   /organizations/{id}/developer                       overview
    GET   /organizations/{id}/developer/tiers                 catalogue + ceiling
    POST  /organizations/{id}/developer/keys                  issue + stamp
    PATCH /organizations/{id}/developer/keys/{key_id}/tier    reassign tier
    GET   /organizations/{id}/developer/keys/{key_id}/metrics consumption
    GET   /organizations/{id}/developer/explorer              code snippets

WHY EVERY HANDLER CALLS `_assert_scope` AND `_assert_human_admin`
=================================================================

`RequireOrgAdmin` proves the caller holds an admin role in SOME organization.
It does not prove it is THIS one — the organization comes from the path, and
without the check an admin of tenant A reaches tenant B's developer portal by
editing the URL. ARCH-20's compliance router carries the same guard for the
same reason; this is not defensive duplication, it is the actual boundary.

`_assert_human_admin` is the second guard and it is the more interesting one.
It refuses an API-key principal outright. Without it, a key holding
`organizations:read` could raise its OWN tier to ENTERPRISE — a key that
grants itself throughput is a privilege-escalation primitive, and the fact
that `api_keys:write` sits in `PERMANENTLY_EXCLUDED_SCOPES` shows ARCH-08
already decided keys must never manage keys. This is that decision applied to
the surface that did not exist yet.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api import deps
from app.core.config import settings
from app.core.exceptions import OrganizationPermissionDeniedError
from app.core.principal import PrincipalKind, get_current_principal
from app.core.scopes import ApiKeyScope
from app.crud import api_key as api_key_crud
from app.schemas.developer import (
    ApiExplorerOperation,
    ApiExplorerResponse,
    CodeSnippetSet,
    DeveloperKeyCreateRequest,
    DeveloperKeyIssuedResponse,
    DeveloperKeyMetricsResponse,
    DeveloperKeySummary,
    DeveloperOverviewResponse,
    DeveloperTierUpdateRequest,
    TierCataloguePayload,
)
from app.services import api_key_service, developer_portal_service

logger = logging.getLogger("app.api.v1.developer")

router = APIRouter(tags=["Developer Platform"])

BASE = "/organizations/{organization_id}/developer"


def _assert_scope(
    context: deps.OrganizationContext, organization_id: uuid.UUID
) -> None:
    if context.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )


def _assert_human_admin(request: Request) -> None:
    principal = getattr(request.state, "principal", None) or get_current_principal()
    is_key = (
        principal is not None
        and (
            principal.kind == "API_KEY"
            or principal.kind is PrincipalKind.API_KEY
        )
    ) or getattr(request.state, "api_key_id", None) is not None

    if is_key:
        raise OrganizationPermissionDeniedError(
            "Developer platform management requires a human administrator "
            "session. An API key cannot change its own tier."
        )


def _summary(key: Any, **counters: Any) -> DeveloperKeySummary:
    from app.core.scopes import PUBLIC_API_SCOPES

    public_values = {scope.value for scope in PUBLIC_API_SCOPES}
    return DeveloperKeySummary(
        id=str(key.id),
        name=key.name,
        display_prefix=key.display_prefix,
        tier_key=key.tier_key,
        rate_limit_per_minute=key.rate_limit_per_minute,
        monthly_request_quota=key.monthly_request_quota,
        is_public_api_enabled=key.is_public_api_enabled,
        scopes=list(key.scopes),
        public_scopes=sorted(s for s in key.scopes if s in public_values),
        expires_at=key.expires_at.isoformat() if key.expires_at else None,
        last_used_at=key.last_used_at.isoformat() if key.last_used_at else None,
        month_to_date_requests=int(counters.get("month_to_date_requests", 0)),
        window_requests=int(counters.get("window_requests", 0)),
        quota_used_fraction=counters.get("quota_used_fraction"),
        created_at=key.created_at.isoformat(),
    )


# ===========================================================================
# Reads
# ===========================================================================


@router.get(
    BASE,
    response_model=DeveloperOverviewResponse,
    summary="Developer platform overview",
)
def get_overview(
    organization_id: uuid.UUID,
    db: deps.DbSession,
    request: Request,
    days: int = Query(
        developer_portal_service.DEFAULT_WINDOW_DAYS,
        ge=1,
        le=developer_portal_service.MAX_WINDOW_DAYS,
    ),
    context: deps.OrganizationContext = Depends(deps.RequireOrgAdmin),
) -> Any:
    _assert_scope(context, organization_id)
    _assert_human_admin(request)
    return developer_portal_service.organization_overview(
        db, organization_id=organization_id, days=days
    )


@router.get(
    f"{BASE}/tiers",
    response_model=TierCataloguePayload,
    summary="Rate tiers and this organization's assignment ceiling",
)
def get_tiers(
    organization_id: uuid.UUID,
    db: deps.DbSession,
    request: Request,
    context: deps.OrganizationContext = Depends(deps.RequireOrgAdmin),
) -> Any:
    _assert_scope(context, organization_id)
    _assert_human_admin(request)
    return developer_portal_service.tier_catalogue(
        db, organization_id=organization_id
    ).as_payload()


@router.get(
    f"{BASE}/keys/{{key_id}}/metrics",
    response_model=DeveloperKeyMetricsResponse,
    summary="Consumption and latency for one key",
)
def get_key_metrics(
    organization_id: uuid.UUID,
    key_id: uuid.UUID,
    db: deps.ReadDbSession,
    request: Request,
    days: int = Query(
        developer_portal_service.DEFAULT_WINDOW_DAYS,
        ge=1,
        le=developer_portal_service.MAX_WINDOW_DAYS,
    ),
    context: deps.OrganizationContext = Depends(deps.RequireOrgAdmin),
) -> Any:
    """Reads from the replica.

    Safe here and deliberately not elsewhere in this router: this is a pure
    read of a rollup that is already minutes stale by construction, so replica
    lag adds nothing a caller could notice. The tier write below stays on the
    primary — a write-after-read on a lagging standby would let two admins
    each read the old tier and clobber each other.
    """
    _assert_scope(context, organization_id)
    _assert_human_admin(request)

    key = api_key_crud.get_api_key_by_id(
        db, organization_id=organization_id, key_id=key_id
    )
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key not found."
        )

    return developer_portal_service.key_metrics(
        db,
        organization_id=organization_id,
        api_key_id=key_id,
        days=days,
    )


@router.get(
    f"{BASE}/explorer",
    response_model=ApiExplorerResponse,
    summary="Interactive API explorer operations and code snippets",
)
def get_explorer(
    organization_id: uuid.UUID,
    request: Request,
    workspace_id: Optional[uuid.UUID] = Query(
        None,
        description=(
            "Interpolated into the snippets when supplied, so a developer "
            "can copy a call that actually runs instead of one with a "
            "placeholder they have to hunt down."
        ),
    ),
    context: deps.OrganizationContext = Depends(deps.RequireOrgAdmin),
) -> Any:
    _assert_scope(context, organization_id)
    _assert_human_admin(request)

    base_url = str(request.base_url).rstrip("/") + settings.API_V1_STR
    ws = str(workspace_id) if workspace_id else "<workspace_id>"

    operations = [
        (
            "listDocuments",
            "GET",
            f"/public/documents?workspace_id={ws}",
            "List documents in a workspace",
            ApiKeyScope.PUBLIC_DOCUMENTS_READ,
            None,
        ),
        (
            "runQuery",
            "POST",
            "/public/query",
            "Hybrid retrieval over a workspace",
            ApiKeyScope.PUBLIC_QUERY_WRITE,
            {
                "workspace_id": ws,
                "query": "What are the payment terms?",
                "top_k": 5,
            },
        ),
        (
            "listWorkflows",
            "GET",
            f"/public/workflows?workspace_id={ws}",
            "List automation rules",
            ApiKeyScope.PUBLIC_WORKFLOWS_READ,
            None,
        ),
        (
            "triggerWorkflow",
            "POST",
            "/public/workflows/<rule_id>/trigger",
            "Raise an automation event for a document",
            ApiKeyScope.PUBLIC_WORKFLOWS_WRITE,
            {"workspace_id": ws, "work_item_id": "<work_item_id>"},
        ),
    ]

    return ApiExplorerResponse(
        base_url=base_url,
        operations=[
            ApiExplorerOperation(
                operation_id=op_id,
                method=method,
                path=path,
                summary=summary,
                required_scope=scope.value,
                snippets=CodeSnippetSet(
                    **developer_portal_service.code_snippets(
                        base_url=base_url,
                        path=path,
                        method=method,
                        body=body,
                    )
                ),
            )
            for op_id, method, path, summary, scope, body in operations
        ],
    )


# ===========================================================================
# Writes
# ===========================================================================


@router.post(
    f"{BASE}/keys",
    response_model=DeveloperKeyIssuedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a gateway API key",
)
def issue_key(
    organization_id: uuid.UUID,
    payload: DeveloperKeyCreateRequest,
    db: deps.DbSession,
    request: Request,
    context: deps.OrganizationContext = Depends(deps.RequireOrgAdmin),
) -> Any:
    """Mint a key and stamp its tier in one transaction.

    Two steps rather than one because `issue_api_key` is ARCH-08's and has no
    concept of a tier; extending its signature would put a gateway concern in
    the console's issuance path, where the correct default is exactly the one
    it already has — no tier, gateway disabled.
    """
    _assert_scope(context, organization_id)
    _assert_human_admin(request)

    key, token = api_key_service.issue_api_key(
        db,
        organization_id=organization_id,
        actor=context.membership,
        name=payload.name,
        scopes=[scope.value for scope in payload.scopes],
        expires_at=payload.expires_at,
    )

    try:
        developer_portal_service.assign_tier(
            db,
            key=key,
            tier=payload.tier_key,
            actor=context.membership,
            enable_public_api=payload.enable_public_api,
        )
    except developer_portal_service.TierCeilingExceededError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except developer_portal_service.DeveloperPortalError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    db.commit()
    db.refresh(key)

    return DeveloperKeyIssuedResponse(api_key=_summary(key), token=token)


@router.patch(
    f"{BASE}/keys/{{key_id}}/tier",
    response_model=DeveloperKeySummary,
    summary="Reassign an API key's rate tier",
)
def update_key_tier(
    organization_id: uuid.UUID,
    key_id: uuid.UUID,
    payload: DeveloperTierUpdateRequest,
    db: deps.DbSession,
    request: Request,
    context: deps.OrganizationContext = Depends(deps.RequireOrgAdmin),
) -> Any:
    _assert_scope(context, organization_id)
    _assert_human_admin(request)

    key = api_key_crud.get_api_key_by_id(
        db, organization_id=organization_id, key_id=key_id
    )
    if key is None or key.deactivated_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found or revoked.",
        )

    try:
        developer_portal_service.assign_tier(
            db,
            key=key,
            tier=payload.tier_key,
            actor=context.membership,
            enable_public_api=payload.enable_public_api,
        )
    except developer_portal_service.TierCeilingExceededError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except developer_portal_service.DeveloperPortalError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    db.commit()
    db.refresh(key)

    return _summary(key)


__all__ = ["BASE", "router"]