"""ARCH-22 / ARCH-23 — enterprise BYOK credentials and per-tenant model routing.

    GET    /organizations/{id}/byok                       overview        [ADMIN]
    GET    /organizations/{id}/byok/providers             catalogue       [ADMIN]
    GET    /organizations/{id}/byok/credentials           list            [ADMIN]
    PUT    /organizations/{id}/byok/credentials           upsert/rotate   [OWNER]
    DELETE /organizations/{id}/byok/credentials/{provider} retire         [OWNER]
    POST   /organizations/{id}/byok/credentials/{provider}/validate       [OWNER]
    PUT    /organizations/{id}/byok/credentials/{provider}/fallback       [OWNER]
    GET    /organizations/{id}/byok/routes                list            [ADMIN]
    PUT    /organizations/{id}/byok/routes                upsert          [OWNER]
    DELETE /organizations/{id}/byok/routes/{task_type}    delete          [OWNER]
    GET    /organizations/{id}/byok/savings               cost summary    [ADMIN]
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    OrganizationContext,
    RequireOrgAdmin,
    RequireOrgOwner,
    get_db,
)
from app.core.client_ip import client_ip
from app.core.byok_providers import (
    BYOK_PROVIDER_VALUES,
    BYOK_TASK_TYPE_VALUES,
    PROVIDER_REGISTRY,
    TASK_LABELS,
    UnknownProviderError,
    fallback_is_possible,
    is_routable,
    normalize_task_type,
    providers_for_task,
    spec_for,
    unroutable_reason,
)
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.supplier_cogs import SOURCE_ZERO_BYOK
from app.models.usage_event import UsageEvent
from app.schemas.byok import (
    BYOKOverviewResponse,
    BYOKSavingsResponse,
    CredentialValidationResponse,
    FallbackPolicyUpdate,
    ModelRouteResponse,
    ModelRouteUpsert,
    ProviderCatalogEntry,
    ProviderCredentialResponse,
    ProviderCredentialUpsert,
    TaskCatalogEntry,
)
from app.services import audit_service
from app.services.byok import credential_service, model_routing_service
from app.services.byok.credential_service import (
    CredentialError,
    CredentialNotFoundError,
    CredentialShapeError,
)
from app.services.byok.model_routing_service import (
    RoutingError,
    UnroutableProviderError,
)
from app.services.byok.provider_clients import ProviderClientFactory

logger = logging.getLogger("app.api.v1.byok")

router = APIRouter(tags=["Enterprise BYOK"])

BASE = "/organizations/{organization_id}/byok"

DEFAULT_SAVINGS_WINDOW_DAYS = 30
MAX_SAVINGS_WINDOW_DAYS = 365


# ---------------------------------------------------------------------------
# Guards and helpers
# ---------------------------------------------------------------------------


def _assert_scope(
    context: OrganizationContext, organization_id: uuid.UUID
) -> None:
    if context.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )


def _client_context(request: Request) -> dict[str, Optional[str]]:
    """Audit attribution for one request.

    ARCH-23 remediation B-1. This read `request.client.host` directly, which
    behind the ingress is the LOAD BALANCER, not the user. Every audit row the
    BYOK endpoints write — credential creation, rotation, deactivation,
    fallback-policy change — recorded that address instead of the caller's.

    Those are the most security-sensitive audit rows in the platform, and the
    failure was silent: the field was populated, plausible, and identical for
    every tenant on the cluster.

    `app.core.client_ip.client_ip` is the single X-Forwarded-For parser
    established by ARCH-19 and enforced by its gate check G3. It applies
    TRUSTED_PROXY_HOPS and returns None rather than an untrusted address when
    the chain cannot be validated — a null IP is honest, a wrong one is not.
    """
    return {
        "ip_address": client_ip(request),
        "user_agent": request.headers.get("user-agent"),
    }


def _resolved_provider(provider: str) -> str:
    try:
        return spec_for(provider).key
    except UnknownProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


def _credential_payload(credential: Any) -> ProviderCredentialResponse:
    return ProviderCredentialResponse(
        id=credential.id,
        provider=credential.provider,
        status=credential.status,
        is_routable=is_routable(credential.provider),
        unroutable_reason=unroutable_reason(credential.provider),
        key_version=credential.key_version,
        key_fingerprint=credential.key_fingerprint,
        key_last_four=credential.key_last_four,
        allow_platform_fallback=credential.allow_platform_fallback,
        resource_endpoint=credential.resource_endpoint,
        deployment_name=credential.deployment_name,
        is_shape_complete=credential.is_shape_complete,
        fallback_is_possible=fallback_is_possible(credential.provider),
        last_validated_at=credential.last_validated_at,
        last_validation_latency_ms=credential.last_validation_latency_ms,
        validation_error=credential.validation_error,
        last_used_at=credential.last_used_at,
        created_at=credential.created_at,
        updated_at=credential.updated_at,
    )


def _route_payload(
    db: Session, *, organization_id: uuid.UUID, route: Any
) -> ModelRouteResponse:
    decision = model_routing_service.resolve(
        db, organization_id=organization_id, task_type=route.task_type
    )
    return ModelRouteResponse(
        id=route.id,
        task_type=route.task_type,
        task_label=TASK_LABELS.get(route.task_type, route.task_type),
        provider=route.provider,
        model_name=route.model_name,
        use_tenant_key=route.use_tenant_key,
        is_enabled=route.is_enabled,
        effective_tenant_key=decision.use_tenant_key,
        downgrade_reason=decision.downgrade_reason,
        created_at=route.created_at,
        updated_at=route.updated_at,
    )


def _provider_catalogue() -> list[ProviderCatalogEntry]:
    entries: list[ProviderCatalogEntry] = []
    for key in BYOK_PROVIDER_VALUES:
        spec = PROVIDER_REGISTRY[key]
        entries.append(
            ProviderCatalogEntry(
                provider=key,
                label=spec.label,
                is_routable=spec.is_routable,
                unroutable_reason=spec.unroutable_reason,
                key_prefix=spec.key_prefix,
                platform_key_available=bool(
                    ProviderClientFactory.platform_key(key)
                ),
                suggested_models=list(spec.suggested_models),
                requires_endpoint=spec.requires_endpoint,
                endpoint_suffix=spec.endpoint_suffix,
                supported_tasks=list(spec.supported_tasks),
            )
        )
    return entries


def _task_catalogue() -> list[TaskCatalogEntry]:
    return [
        TaskCatalogEntry(
            task_type=task,
            label=TASK_LABELS.get(task, task),
            eligible_providers=list(providers_for_task(task)),
        )
        for task in BYOK_TASK_TYPE_VALUES
    ]


def _savings(
    db: Session, *, organization_id: uuid.UUID, window_days: int
) -> BYOKSavingsResponse:
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    is_byok = UsageEvent.cost_basis_source == SOURCE_ZERO_BYOK

    row = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(case((is_byok, 1), else_=0)), 0),
            func.coalesce(
                func.sum(case((is_byok, UsageEvent.quantity), else_=0)), 0
            ),
            func.coalesce(
                func.sum(
                    case((is_byok, 0), else_=UsageEvent.cost_basis_micros)
                ),
                0,
            ),
        ).where(
            UsageEvent.organization_id == organization_id,
            UsageEvent.occurred_at >= since,
        )
    ).one()

    total_events = int(row[0] or 0)
    byok_events = int(row[1] or 0)
    platform_events = max(total_events - byok_events, 0)
    share = (byok_events / total_events * 100.0) if total_events else 0.0

    return BYOKSavingsResponse(
        window_days=window_days,
        byok_events=byok_events,
        platform_events=platform_events,
        byok_tokens=int(row[2] or 0),
        platform_cost_micros=int(row[3] or 0),
        byok_share_percent=round(share, 2),
    )


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@router.get(
    BASE,
    response_model=BYOKOverviewResponse,
    summary="Provider credentials, routing rules and BYOK cost share",
)
def get_byok_overview(
    organization_id: uuid.UUID,
    window_days: int = Query(
        DEFAULT_SAVINGS_WINDOW_DAYS, ge=1, le=MAX_SAVINGS_WINDOW_DAYS
    ),
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> BYOKOverviewResponse:
    _assert_scope(context, organization_id)

    credentials = credential_service.list_for_organization(
        db, organization_id=organization_id
    )
    routes = model_routing_service.list_routes(
        db, organization_id=organization_id
    )

    return BYOKOverviewResponse(
        organization_id=organization_id,
        providers=_provider_catalogue(),
        tasks=_task_catalogue(),
        credentials=[_credential_payload(c) for c in credentials],
        routes=[
            _route_payload(db, organization_id=organization_id, route=r)
            for r in routes
        ],
        savings=_savings(
            db, organization_id=organization_id, window_days=window_days
        ),
        routable_provider_count=sum(
            1 for spec in PROVIDER_REGISTRY.values() if spec.is_routable
        ),
        active_credential_count=len(credentials),
    )


@router.get(
    f"{BASE}/providers",
    response_model=list[ProviderCatalogEntry],
    summary="Every provider this build knows, and whether it can be routed",
)
def list_providers(
    organization_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> list[ProviderCatalogEntry]:
    _assert_scope(context, organization_id)
    return _provider_catalogue()


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


@router.get(
    f"{BASE}/credentials",
    response_model=list[ProviderCredentialResponse],
    summary="Stored provider credentials (never the keys themselves)",
)
def list_credentials(
    organization_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> list[ProviderCredentialResponse]:
    _assert_scope(context, organization_id)
    return [
        _credential_payload(credential)
        for credential in credential_service.list_for_organization(
            db, organization_id=organization_id
        )
    ]


@router.put(
    f"{BASE}/credentials",
    response_model=ProviderCredentialResponse,
    summary="Store or rotate a provider API key",
)
def upsert_credential(
    organization_id: uuid.UUID,
    payload: ProviderCredentialUpsert,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> ProviderCredentialResponse:
    _assert_scope(context, organization_id)
    provider = _resolved_provider(payload.provider)

    existing = credential_service.resolve_active(
        db, organization_id=organization_id, provider=provider
    )
    rotating = existing is not None

    try:
        credential = credential_service.upsert_credential(
            db,
            organization_id=organization_id,
            provider=provider,
            plaintext_key=payload.api_key.get_secret_value(),
            allow_platform_fallback=payload.allow_platform_fallback,
            actor_id=context.user_id,
            resource_endpoint=payload.resource_endpoint,
            deployment_name=payload.deployment_name,
        )
    except (CredentialError, CredentialShapeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.PROVIDER_CREDENTIAL,
        resource_id=credential.id,
        action=AuditAction.ROTATED if rotating else AuditAction.CREATED,
        details={
            "provider": provider,
            "key_version": credential.key_version,
            "key_fingerprint": credential.key_fingerprint,
            "routable": is_routable(provider),
            "resource_endpoint": credential.resource_endpoint,
        },
        **_client_context(request),
    )
    db.commit()
    db.refresh(credential)
    return _credential_payload(credential)


@router.delete(
    f"{BASE}/credentials/{{provider}}",
    response_model=ProviderCredentialResponse,
    summary="Retire a provider credential",
)
def delete_credential(
    organization_id: uuid.UUID,
    provider: str,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> ProviderCredentialResponse:
    _assert_scope(context, organization_id)
    key = _resolved_provider(provider)

    try:
        credential = credential_service.deactivate(
            db, organization_id=organization_id, provider=key
        )
    except CredentialNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.PROVIDER_CREDENTIAL,
        resource_id=credential.id,
        action=AuditAction.DELETED,
        details={"provider": key, "key_version": credential.key_version},
        **_client_context(request),
    )
    db.commit()
    db.refresh(credential)
    return _credential_payload(credential)


@router.post(
    f"{BASE}/credentials/{{provider}}/validate",
    response_model=CredentialValidationResponse,
    summary="Run a live round trip against the provider",
)
def validate_credential(
    organization_id: uuid.UUID,
    provider: str,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> CredentialValidationResponse:
    _assert_scope(context, organization_id)
    key = _resolved_provider(provider)

    try:
        credential, outcome = credential_service.validate_and_record(
            db, organization_id=organization_id, provider=key
        )
    except CredentialNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except CredentialError as exc:
        logger.error(
            "byok.validation_aborted",
            extra={"organization_id": str(organization_id), "provider": key},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.PROVIDER_CREDENTIAL,
        resource_id=credential.id,
        action=AuditAction.CREDENTIAL_VALIDATED,
        details={
            "provider": key,
            "ok": outcome.ok,
            "latency_ms": outcome.latency_ms,
        },
        **_client_context(request),
    )
    db.commit()
    db.refresh(credential)

    return CredentialValidationResponse(
        provider=key,
        ok=outcome.ok,
        latency_ms=outcome.latency_ms,
        error=outcome.truncated_error,
        checked_at=outcome.checked_at,
        credential=_credential_payload(credential),
    )


@router.put(
    f"{BASE}/credentials/{{provider}}/fallback",
    response_model=ProviderCredentialResponse,
    summary="Permit or forbid falling back to the platform provider account",
)
def update_fallback_policy(
    organization_id: uuid.UUID,
    provider: str,
    payload: FallbackPolicyUpdate,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> ProviderCredentialResponse:
    _assert_scope(context, organization_id)
    key = _resolved_provider(provider)

    try:
        credential = credential_service.set_fallback_policy(
            db,
            organization_id=organization_id,
            provider=key,
            allow_platform_fallback=payload.allow_platform_fallback,
        )
    except CredentialNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.PROVIDER_CREDENTIAL,
        resource_id=credential.id,
        action=AuditAction.FALLBACK_POLICY_CHANGED,
        details={
            "provider": key,
            "allow_platform_fallback": payload.allow_platform_fallback,
        },
        **_client_context(request),
    )
    db.commit()
    db.refresh(credential)
    return _credential_payload(credential)


# ---------------------------------------------------------------------------
# Routing rules
# ---------------------------------------------------------------------------


@router.get(
    f"{BASE}/routes",
    response_model=list[ModelRouteResponse],
    summary="Per-task provider and model routing rules",
)
def list_routes(
    organization_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> list[ModelRouteResponse]:
    _assert_scope(context, organization_id)
    return [
        _route_payload(db, organization_id=organization_id, route=route)
        for route in model_routing_service.list_routes(
            db, organization_id=organization_id
        )
    ]


@router.put(
    f"{BASE}/routes",
    response_model=ModelRouteResponse,
    summary="Point one pipeline task at a provider and model",
)
def upsert_route(
    organization_id: uuid.UUID,
    payload: ModelRouteUpsert,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> ModelRouteResponse:
    _assert_scope(context, organization_id)

    try:
        route = model_routing_service.upsert_route(
            db,
            organization_id=organization_id,
            task_type=payload.task_type,
            provider=payload.provider,
            model_name=payload.model_name,
            use_tenant_key=payload.use_tenant_key,
            is_enabled=payload.is_enabled,
        )
    except (UnroutableProviderError, RoutingError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.MODEL_ROUTE,
        resource_id=route.id,
        action=AuditAction.UPDATED,
        details={
            "task_type": route.task_type,
            "provider": route.provider,
            "model_name": route.model_name,
            "use_tenant_key": route.use_tenant_key,
        },
        **_client_context(request),
    )
    db.commit()
    db.refresh(route)
    return _route_payload(db, organization_id=organization_id, route=route)


@router.delete(
    f"{BASE}/routes/{{task_type}}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Return a task to the workspace AI settings default",
)
def delete_route(
    organization_id: uuid.UUID,
    task_type: str,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Response:
    _assert_scope(context, organization_id)
    task = normalize_task_type(task_type)

    if task not in BYOK_TASK_TYPE_VALUES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{task_type}' is not a known task type.",
        )

    removed = model_routing_service.delete_route(
        db, organization_id=organization_id, task_type=task
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No routing rule is configured for {task}.",
        )

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.MODEL_ROUTE,
        resource_id=organization_id,
        action=AuditAction.DELETED,
        details={"task_type": task},
        **_client_context(request),
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Savings
# ---------------------------------------------------------------------------


@router.get(
    f"{BASE}/savings",
    response_model=BYOKSavingsResponse,
    summary="Share of this tenant's usage funded by their own provider keys",
)
def get_savings(
    organization_id: uuid.UUID,
    window_days: int = Query(
        DEFAULT_SAVINGS_WINDOW_DAYS, ge=1, le=MAX_SAVINGS_WINDOW_DAYS
    ),
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> BYOKSavingsResponse:
    _assert_scope(context, organization_id)
    return _savings(
        db, organization_id=organization_id, window_days=window_days
    )


__all__ = ["router"]