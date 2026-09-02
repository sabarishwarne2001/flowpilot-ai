"""ARCH-09 Step 8a — webhook management API."""

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.deps import (
    DbSession,
    OrgAdminCtx,
    RequireOrgAdmin,
    RequireScope,
    get_current_principal,
    get_db,
)
from app.core.scopes import ApiKeyScope, effective_scopes
from app.core.webhook_events import WEBHOOK_EVENT_TYPES, sorted_event_types
from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus
from app.models.webhook_delivery_attempt import WebhookDeliveryAttempt
from app.models.webhook_endpoint import WebhookEndpoint, WebhookEndpointStatus
from app.services import circuit_breaker, webhook_service

router = APIRouter(
    prefix="/organizations/{organization_id}/webhooks", tags=["webhooks"]
)


# ======================================================================
# Schemas
# ======================================================================
class EndpointCreate(BaseModel):
    url: str = Field(..., max_length=2000)
    event_types: list[str] = Field(..., min_length=1)
    description: Optional[str] = Field(None, max_length=500)

    @field_validator("url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("Webhook URLs must use https://.")
        return v

    @field_validator("event_types")
    @classmethod
    def _known_events(cls, v: list[str]) -> list[str]:
        unknown = sorted(set(v) - WEBHOOK_EVENT_TYPES)
        if unknown:
            raise ValueError(
                f"Unknown event type(s): {unknown}. "
                f"Known: {', '.join(sorted_event_types())}"
            )
        return list(dict.fromkeys(v))


class EndpointUpdate(BaseModel):
    url: Optional[str] = Field(None, max_length=2000)
    event_types: Optional[list[str]] = Field(None, min_length=1)
    description: Optional[str] = Field(None, max_length=500)
    status: Optional[Literal["ACTIVE", "DISABLED"]] = None

    @field_validator("url")
    @classmethod
    def _https_only(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not v.startswith("https://"):
            raise ValueError("Webhook URLs must use https://.")
        return v

    @field_validator("event_types")
    @classmethod
    def _known_events(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        unknown = sorted(set(v) - WEBHOOK_EVENT_TYPES)
        if unknown:
            raise ValueError(
                f"Unknown event type(s): {unknown}. "
                f"Known: {', '.join(sorted_event_types())}"
            )
        return list(dict.fromkeys(v))


class EndpointOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    url: str
    description: Optional[str]
    event_types: list[str]
    status: str
    auto_disabled: bool
    disabled_at: Optional[datetime]
    disabled_reason: Optional[str]
    consecutive_failures: int
    last_success_at: Optional[datetime]
    last_failure_at: Optional[datetime]
    secret_last_rotated_at: datetime
    rotation_overlap_until: Optional[datetime]
    created_at: datetime

    @classmethod
    def of(cls, e: WebhookEndpoint) -> "EndpointOut":
        return cls(
            id=e.id,
            organization_id=e.organization_id,
            url=e.url,
            description=e.description,
            event_types=list(e.event_types or []),
            status=e.status.value if e.status else "ACTIVE",
            auto_disabled=bool(e.auto_disabled),
            disabled_at=e.disabled_at,
            disabled_reason=e.disabled_reason,
            consecutive_failures=e.consecutive_failures,
            last_success_at=e.last_success_at,
            last_failure_at=e.last_failure_at,
            secret_last_rotated_at=e.secret_last_rotated_at,
            rotation_overlap_until=e.previous_secret_expires_at,
            created_at=e.created_at,
        )


class EndpointCreated(BaseModel):
    endpoint: EndpointOut
    secret: str = Field(
        ...,
        description="Store this now. FlowPilot cannot show it again.",
    )


class RotateSecretOut(BaseModel):
    secret: str
    previous_secret_valid_until: datetime
    note: str = (
        "Both the new and previous secrets sign deliveries until the overlap expires."
    )


class DeliveryOut(BaseModel):
    id: uuid.UUID
    webhook_endpoint_id: uuid.UUID
    event_type: str
    status: str
    attempts: int
    available_at: datetime
    delivered_at: Optional[datetime]
    last_response_status: Optional[int]
    last_error: Optional[str]
    created_at: datetime

    @classmethod
    def of(cls, d: WebhookDelivery) -> "DeliveryOut":
        return cls(
            id=d.id,
            webhook_endpoint_id=d.webhook_endpoint_id,
            event_type=d.event_type,
            status=d.status.value,
            attempts=d.attempts,
            available_at=d.available_at,
            delivered_at=d.delivered_at,
            last_response_status=d.last_response_status,
            last_error=d.last_error,
            created_at=d.created_at,
        )


class AttemptOut(BaseModel):
    id: uuid.UUID
    attempt_number: int
    disposition: str
    response_status: Optional[int]
    duration_ms: int
    error: Optional[str]
    resolved_ip: Optional[str]
    attempted_at: datetime
    response_body_excerpt: Optional[str] = None
    request_headers: Optional[dict[str, Any]] = None


# ======================================================================
# Tenancy lookups
# ======================================================================
def _get_endpoint(
    db: Session, organization_id: uuid.UUID, endpoint_id: uuid.UUID
) -> WebhookEndpoint:
    endpoint = db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.id == endpoint_id,
            WebhookEndpoint.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found.")
    return endpoint


def _get_delivery(
    db: Session, organization_id: uuid.UUID, delivery_id: uuid.UUID
) -> WebhookDelivery:
    delivery = db.execute(
        select(WebhookDelivery).where(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery not found.")
    return delivery


def _caller_has_webhook_admin_scope(request: Request, db: Session) -> bool:
    principal = getattr(request.state, "principal", None) or get_current_principal()
    if principal is None or principal.kind != "API_KEY":
        return True

    key_obj = getattr(request.state, "api_key_obj", None)
    membership_obj = getattr(request.state, "api_key_membership", None)
    if not key_obj or not membership_obj:
        return False
    return ApiKeyScope.WEBHOOKS_ADMIN in effective_scopes(key_obj, membership_obj)


def _serialise_attempt(attempt: WebhookDeliveryAttempt, *, include_sensitive: bool) -> AttemptOut:
    out = AttemptOut(
        id=attempt.id,
        attempt_number=attempt.attempt_number,
        disposition=attempt.disposition.value,
        response_status=attempt.response_status,
        duration_ms=attempt.duration_ms,
        error=attempt.error,
        resolved_ip=attempt.resolved_ip,
        attempted_at=attempt.attempted_at,
    )
    if include_sensitive:
        out.response_body_excerpt = attempt.response_body_excerpt
        out.request_headers = attempt.request_headers
    return out


# ======================================================================
# Endpoints — CRUD
# ======================================================================
@router.post(
    "/endpoints",
    response_model=EndpointCreated,
    status_code=201,
    dependencies=[Depends(RequireScope(ApiKeyScope.WEBHOOKS_WRITE))],
)
def create_endpoint(
    body: EndpointCreate,
    context: OrgAdminCtx,
    db: DbSession,
) -> EndpointCreated:
    """Register an endpoint. Returns the plaintext secret exactly once."""
    try:
        endpoint, secret = webhook_service.register_endpoint(
            db,
            organization_id=context.organization_id,
            url=body.url,
            event_types=body.event_types,
            created_by_user_id=context.user_id,
            description=body.description,
        )
    except webhook_service.InvalidURLError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except webhook_service.InvalidEventTypesError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db.commit()
    db.refresh(endpoint)
    return EndpointCreated(endpoint=EndpointOut.of(endpoint), secret=secret)


@router.get(
    "/endpoints",
    response_model=list[EndpointOut],
    dependencies=[Depends(RequireScope(ApiKeyScope.WEBHOOKS_READ))],
)
def list_endpoints(
    context: OrgAdminCtx,
    db: DbSession,
    status_filter: Optional[Literal["ACTIVE", "DISABLED"]] = Query(None, alias="status"),
) -> list[EndpointOut]:
    stmt = select(WebhookEndpoint).where(
        WebhookEndpoint.organization_id == context.organization_id
    )
    if status_filter:
        stmt = stmt.where(WebhookEndpoint.status == WebhookEndpointStatus(status_filter))
    stmt = stmt.order_by(WebhookEndpoint.created_at.desc())
    return [EndpointOut.of(e) for e in db.execute(stmt).scalars().all()]


@router.get(
    "/endpoints/{endpoint_id}",
    response_model=EndpointOut,
    dependencies=[Depends(RequireScope(ApiKeyScope.WEBHOOKS_READ))],
)
def get_endpoint(
    endpoint_id: uuid.UUID,
    context: OrgAdminCtx,
    db: DbSession,
) -> EndpointOut:
    return EndpointOut.of(_get_endpoint(db, context.organization_id, endpoint_id))


@router.patch(
    "/endpoints/{endpoint_id}",
    response_model=EndpointOut,
    dependencies=[Depends(RequireScope(ApiKeyScope.WEBHOOKS_WRITE))],
)
def update_endpoint(
    endpoint_id: uuid.UUID,
    body: EndpointUpdate,
    context: OrgAdminCtx,
    db: DbSession,
) -> EndpointOut:
    endpoint = _get_endpoint(db, context.organization_id, endpoint_id)

    if body.url is not None and body.url != endpoint.url:
        try:
            webhook_service._preflight_check_url(body.url)
        except webhook_service.InvalidURLError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        endpoint.url = body.url

    if body.event_types is not None:
        endpoint.event_types = body.event_types
    if body.description is not None:
        endpoint.description = body.description

    if body.status is not None:
        if body.status == "DISABLED" and endpoint.is_active:
            webhook_service.disable_endpoint(
                db,
                endpoint,
                disabled_by_user_id=context.user_id,
                reason="Manually disabled by an administrator.",
            )
            endpoint.auto_disabled = False
        elif body.status == "ACTIVE" and not endpoint.is_active:
            webhook_service.enable_endpoint(db, endpoint)
            circuit_breaker.reset_breaker(db, endpoint)

    db.commit()
    db.refresh(endpoint)
    return EndpointOut.of(endpoint)


@router.delete(
    "/endpoints/{endpoint_id}",
    status_code=204,
    dependencies=[Depends(RequireScope(ApiKeyScope.WEBHOOKS_WRITE))],
)
def delete_endpoint(
    endpoint_id: uuid.UUID,
    context: OrgAdminCtx,
    db: DbSession,
) -> Response:
    endpoint = _get_endpoint(db, context.organization_id, endpoint_id)
    db.delete(endpoint)
    db.commit()
    return Response(status_code=204)


# ======================================================================
# Secret rotation
# ======================================================================
@router.post(
    "/endpoints/{endpoint_id}/rotate-secret",
    response_model=RotateSecretOut,
    dependencies=[Depends(RequireScope(ApiKeyScope.WEBHOOKS_ADMIN))],
)
def rotate_secret(
    endpoint_id: uuid.UUID,
    context: OrgAdminCtx,
    db: DbSession,
    overlap_days: int = Query(webhook_service.SECRET_OVERLAP_DAYS, ge=0, le=30),
) -> RotateSecretOut:
    endpoint = _get_endpoint(db, context.organization_id, endpoint_id)
    secret = webhook_service.rotate_secret(db, endpoint, overlap_days=overlap_days)
    db.commit()
    db.refresh(endpoint)
    return RotateSecretOut(
        secret=secret,
        previous_secret_valid_until=endpoint.previous_secret_expires_at,
    )


# ======================================================================
# Delivery history
# ======================================================================
@router.get(
    "/endpoints/{endpoint_id}/deliveries",
    response_model=list[DeliveryOut],
    dependencies=[Depends(RequireScope(ApiKeyScope.WEBHOOKS_READ))],
)
def list_deliveries(
    endpoint_id: uuid.UUID,
    context: OrgAdminCtx,
    db: DbSession,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    before_seq: Optional[int] = Query(
        None,
        description="Keyset cursor on `seq`.",
    ),
) -> list[DeliveryOut]:
    _get_endpoint(db, context.organization_id, endpoint_id)

    stmt = select(WebhookDelivery).where(
        WebhookDelivery.webhook_endpoint_id == endpoint_id,
        WebhookDelivery.organization_id == context.organization_id,
    )
    if status_filter:
        try:
            stmt = stmt.where(WebhookDelivery.status == WebhookDeliveryStatus(status_filter))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown status '{status_filter}'. Valid: "
                    f"{', '.join(s.value for s in WebhookDeliveryStatus)}"
                ),
            ) from exc
    if before_seq is not None:
        stmt = stmt.where(WebhookDelivery.seq < before_seq)

    stmt = stmt.order_by(WebhookDelivery.seq.desc()).limit(limit)
    return [DeliveryOut.of(d) for d in db.execute(stmt).scalars().all()]


@router.get(
    "/deliveries/{delivery_id}/attempts",
    response_model=list[AttemptOut],
    dependencies=[Depends(RequireScope(ApiKeyScope.WEBHOOKS_READ))],
)
def list_attempts(
    delivery_id: uuid.UUID,
    context: OrgAdminCtx,
    db: DbSession,
    request: Request,
) -> list[AttemptOut]:
    _get_delivery(db, context.organization_id, delivery_id)

    attempts = (
        db.execute(
            select(WebhookDeliveryAttempt)
            .where(WebhookDeliveryAttempt.webhook_delivery_id == delivery_id)
            .order_by(WebhookDeliveryAttempt.attempt_number.desc())
        )
        .scalars()
        .all()
    )
    include = _caller_has_webhook_admin_scope(request, db)
    return [_serialise_attempt(a, include_sensitive=include) for a in attempts]


@router.post(
    "/deliveries/{delivery_id}/redeliver",
    response_model=DeliveryOut,
    dependencies=[Depends(RequireScope(ApiKeyScope.WEBHOOKS_WRITE))],
)
def redeliver(
    delivery_id: uuid.UUID,
    context: OrgAdminCtx,
    db: DbSession,
) -> DeliveryOut:
    delivery = _get_delivery(db, context.organization_id, delivery_id)

    if delivery.status is WebhookDeliveryStatus.CLAIMED:
        raise HTTPException(
            status_code=409,
            detail=(
                "This delivery is currently being attempted by a worker. "
                "Wait for the attempt to finish, then retry."
            ),
        )
    if delivery.status is WebhookDeliveryStatus.DELIVERED:
        raise HTTPException(
            status_code=409,
            detail="This delivery already succeeded. Redelivering would send a duplicate.",
        )

    endpoint = _get_endpoint(db, context.organization_id, delivery.webhook_endpoint_id)
    if not endpoint.is_active:
        raise HTTPException(
            status_code=409,
            detail=(
                "The endpoint is disabled: "
                f"{endpoint.disabled_reason or 'no reason recorded'} "
                "Re-enable it before redelivering."
            ),
        )

    delivery.status = WebhookDeliveryStatus.PENDING
    delivery.attempts = 0
    delivery.available_at = datetime.now(timezone.utc)
    delivery.claim_expires_at = None
    delivery.claimed_at = None
    delivery.claimed_by = None
    delivery.last_error = None
    delivery.delivered_at = None
    db.commit()
    db.refresh(delivery)
    return DeliveryOut.of(delivery)
