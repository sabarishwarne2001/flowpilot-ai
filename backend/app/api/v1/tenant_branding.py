"""ARCH-25 §3, §4, §6 — tenant branding endpoints.

    GET    /organizations/{id}/branding                       read    [ADMIN]
    PUT    /organizations/{id}/branding                       tokens  [ADMIN]
    POST   /organizations/{id}/branding/logo                  upload  [ADMIN]
    DELETE /organizations/{id}/branding/logo                  clear   [ADMIN]
    POST   /organizations/{id}/branding/favicon               upload  [ADMIN]
    DELETE /organizations/{id}/branding/favicon               clear   [ADMIN]
    PUT    /organizations/{id}/branding/sender-domain         set     [ADMIN]
    POST   /organizations/{id}/branding/sender-domain/verify  check   [ADMIN]

    GET    /branding/manifest                                 PUBLIC
    GET    /branding/logo                                     PUBLIC
    GET    /branding/favicon                                  PUBLIC

WHY BRANDING IS ADMIN WHILE DOMAINS ARE OWNER
=============================================

Choosing a logo is presentation. Claiming a hostname decides which origin a
tenant's users type their password into. The two live on one console page and
behind two different role gates, which is why they are two routers.

THE PUBLIC ROUTER IS THE PART TO READ CAREFULLY
===============================================

`public_router` carries the only unauthenticated routes in this phase. All
three resolve the tenant SOLELY from `request.state.host_organization_id`,
which `HostTenantMiddleware` sets from an exact match against a VERIFIED
custom domain and sets to None otherwise.

Four properties hold, and each is deliberate:

  1. No path or query parameter identifies a tenant. There is nothing to
     enumerate and nothing to substitute.
  2. `host_organization_id` of None yields the platform default (for the
     manifest) or a 404 (for the assets). It never falls back to a tenant.
  3. The manifest body carries no organization id, slug, name or plan — see
     `BrandingManifest`, whose exact field set verify_arch25.py G12 asserts.
  4. The asset routes return `image/png` bytes or 404. No filename, no id, no
     header naming a tenant.

`public_router` is mounted separately in router.py so that adding a route to
it is a visible act rather than a consequence of file position.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.api.deps import OrganizationContext, RequireOrgAdmin, get_db
from app.core.client_ip import client_ip
from app.core.config import settings
from app.models.organization import Organization
from app.schemas.tenant_branding import (
    BrandingManifest,
    SenderDomainStatusResponse,
    SenderDomainUpdate,
    TenantBrandingResponse,
    TenantBrandingUpdate,
)
from app.services.branding import branding_service
from app.services.branding.errors import CrossTenantAssetError

logger = logging.getLogger("app.api.v1.tenant_branding")

router = APIRouter(tags=["Tenant Branding"])
public_router = APIRouter(tags=["Tenant Branding"])

BASE = "/organizations/{organization_id}/branding"


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
    """See the identical helper in custom_domains.py. ARCH-23 finding B-1."""
    return {
        "ip_address": client_ip(request),
        "user_agent": request.headers.get("user-agent"),
    }


def _response(branding: Any) -> TenantBrandingResponse:
    return TenantBrandingResponse(
        id=branding.id,
        organization_id=branding.organization_id,
        brand_name=branding.brand_name,
        logo_file_id=branding.logo_file_id,
        favicon_file_id=branding.favicon_file_id,
        logo_url=(
            branding_service.LOGO_PUBLIC_PATH if branding.logo_file_id else None
        ),
        favicon_url=(
            branding_service.FAVICON_PUBLIC_PATH
            if branding.favicon_file_id
            else None
        ),
        primary_color=branding.primary_color,
        accent_color=branding.accent_color,
        background_color=branding.background_color,
        foreground_color=branding.foreground_color,
        color_scheme=branding.color_scheme,
        support_email=branding.support_email,
        is_enabled=branding.is_enabled,
        sender=branding_service.sender_status(branding),
        updated_at=branding.updated_at,
    )


def _asset_headers() -> dict[str, str]:
    """Headers for a branding image served to an anonymous visitor.

    `private` would be wrong here and `public` is right: this is a logo on a
    login page, identical for everyone who reaches that hostname, and a CDN or
    browser caching it is the desired behaviour. `nosniff` still applies —
    the bytes are re-encoded PNG produced by Pillow, but declaring the type
    and forbidding sniffing costs nothing and closes the content-type
    confusion path entirely.
    """
    seconds = int(getattr(settings, "BRANDING_MANIFEST_CACHE_SECONDS", 60))
    return {
        "Cache-Control": f"public, max-age={max(0, seconds)}",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
    }


# ---------------------------------------------------------------------------
# Organization console — ADMIN
# ---------------------------------------------------------------------------


@router.get(BASE, response_model=TenantBrandingResponse)
def get_branding(
    organization_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> Any:
    _assert_scope(context, organization_id)
    branding = branding_service.get_or_create_branding(
        db, organization_id=organization_id
    )
    db.commit()
    db.refresh(branding)
    return _response(branding)


@router.put(BASE, response_model=TenantBrandingResponse)
def update_branding(
    organization_id: uuid.UUID,
    payload: TenantBrandingUpdate,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> Any:
    """Update brand tokens.

    The payload has no `logo_file_id` or `favicon_file_id` field at all, and
    `model_config` sets `extra="forbid"`, so sending one is a 422 rather than
    a silently ignored key. Assets move only through the upload endpoints
    below, which are the only paths that can prove the stored object belongs
    to this tenant before the reference is written.
    """
    _assert_scope(context, organization_id)
    branding = branding_service.get_or_create_branding(
        db, organization_id=organization_id
    )
    branding_service.update_branding(
        db,
        branding=branding,
        payload=payload,
        actor_id=context.user_id,
        audit_context=_client_context(request),
    )
    db.commit()
    db.refresh(branding)
    return _response(branding)


def _upload_asset(
    *,
    kind: str,
    organization_id: uuid.UUID,
    file: UploadFile,
    request: Request,
    db: Session,
    context: OrganizationContext,
) -> TenantBrandingResponse:
    _assert_scope(context, organization_id)
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )
    branding = branding_service.get_or_create_branding(
        db, organization_id=organization_id
    )
    branding_service.attach_asset(
        db,
        organization=organization,
        branding=branding,
        kind=kind,
        raw=file.file.read(),
        original_filename=file.filename or f"{kind}.png",
        actor_id=context.user_id,
        audit_context=_client_context(request),
    )
    db.commit()
    db.refresh(branding)
    return _response(branding)


@router.post(BASE + "/logo", response_model=TenantBrandingResponse)
def upload_logo(
    organization_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> Any:
    return _upload_asset(
        kind=branding_service.ASSET_LOGO,
        organization_id=organization_id,
        file=file,
        request=request,
        db=db,
        context=context,
    )


@router.post(BASE + "/favicon", response_model=TenantBrandingResponse)
def upload_favicon(
    organization_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> Any:
    return _upload_asset(
        kind=branding_service.ASSET_FAVICON,
        organization_id=organization_id,
        file=file,
        request=request,
        db=db,
        context=context,
    )


def _clear_asset(
    *,
    kind: str,
    organization_id: uuid.UUID,
    request: Request,
    db: Session,
    context: OrganizationContext,
) -> TenantBrandingResponse:
    _assert_scope(context, organization_id)
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )
    branding = branding_service.get_or_create_branding(
        db, organization_id=organization_id
    )
    branding_service.clear_asset(
        db,
        organization=organization,
        branding=branding,
        kind=kind,
        actor_id=context.user_id,
        audit_context=_client_context(request),
    )
    db.commit()
    db.refresh(branding)
    return _response(branding)


@router.delete(BASE + "/logo", response_model=TenantBrandingResponse)
def clear_logo(
    organization_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> Any:
    return _clear_asset(
        kind=branding_service.ASSET_LOGO,
        organization_id=organization_id,
        request=request,
        db=db,
        context=context,
    )


@router.delete(BASE + "/favicon", response_model=TenantBrandingResponse)
def clear_favicon(
    organization_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> Any:
    return _clear_asset(
        kind=branding_service.ASSET_FAVICON,
        organization_id=organization_id,
        request=request,
        db=db,
        context=context,
    )


@router.put(
    BASE + "/sender-domain", response_model=SenderDomainStatusResponse
)
def set_sender_domain(
    organization_id: uuid.UUID,
    payload: SenderDomainUpdate,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> Any:
    """Set or clear the custom From: domain.

    A newly set domain always lands PENDING, never VERIFIED, and mail keeps
    going out from the platform address until the records actually resolve.
    That is not caution for its own sake: sending as a domain whose SPF does
    not authorise us gets the message filed as spam or rejected outright, and
    the tenant would blame the platform for mail their own DNS refused.
    """
    _assert_scope(context, organization_id)
    branding = branding_service.get_or_create_branding(
        db, organization_id=organization_id
    )
    branding_service.set_sender_domain(
        db,
        branding=branding,
        sender_domain=payload.sender_domain,
        actor_id=context.user_id,
        audit_context=_client_context(request),
    )
    db.commit()
    db.refresh(branding)
    return branding_service.sender_status(branding)


@router.post(
    BASE + "/sender-domain/verify", response_model=SenderDomainStatusResponse
)
def verify_sender_domain(
    organization_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> Any:
    """Check SPF and DKIM now.

    `raise_on_failure=False`: unlike domain verification, a failure here is a
    state the console renders rather than an error it reports. The response
    carries `degradation_reason` — a sentence naming the domain and saying
    mail is going out from the platform address — which is invariant 5's
    visible degradation. Raising would replace that sentence with a generic
    error toast.
    """
    _assert_scope(context, organization_id)
    branding = branding_service.get_or_create_branding(
        db, organization_id=organization_id
    )
    result = branding_service.verify_sender_domain(
        db,
        branding=branding,
        actor_id=context.user_id,
        audit_context=_client_context(request),
        raise_on_failure=False,
    )
    db.commit()
    return result


# ---------------------------------------------------------------------------
# Public — host-resolved, unauthenticated
# ---------------------------------------------------------------------------


def _host_branding(request: Request, db: Session):
    """The tenant for this Host, or None.

    Reads `request.state.host_organization_id`, which HostTenantMiddleware
    sets ONLY from an exact match against a verified custom domain. This
    function performs no lookup of its own and accepts no parameter: there is
    no argument an attacker could supply to change which tenant it returns.
    """
    from app.middleware.host_tenant import host_organization_id

    organization_id = host_organization_id(request)
    if organization_id is None:
        return None, None
    organization = db.get(Organization, organization_id)
    if organization is None:
        return None, None
    branding = branding_service.get_branding(
        db, organization_id=organization_id
    )
    return organization, branding


@public_router.get("/branding/manifest", response_model=BrandingManifest)
def branding_manifest(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> Any:
    """The theme for whichever hostname this request arrived on.

    Returns the platform default for the platform origin, for a tenant with no
    branding row, and for a tenant who has not enabled branding. It never
    returns a tenant's tokens for a Host that did not exactly match a verified
    domain — an unmatched vanity host is refused by the middleware and never
    reaches this handler at all.

    `Vary: Host` is not optional. Without it a shared cache would serve one
    tenant's palette to another tenant's visitors, which is the same
    cross-tenant leak the middleware exists to prevent, arriving through
    Cloudflare instead of through our code.
    """
    _, branding = _host_branding(request, db)
    response.headers["Vary"] = "Host"
    response.headers["Cache-Control"] = (
        f"public, max-age="
        f"{max(0, int(getattr(settings, 'BRANDING_MANIFEST_CACHE_SECONDS', 60)))}"
    )
    return branding_service.build_manifest(branding)


def _serve_asset(request: Request, db: Session, *, kind: str) -> Response:
    organization, branding = _host_branding(request, db)
    if organization is None or branding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not Found"
        )
    try:
        record = branding_service.resolve_asset(db, branding=branding, kind=kind)
        payload = branding_service.read_asset_bytes(
            organization=organization, record=record
        )
    except CrossTenantAssetError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not Found"
        )
    headers = _asset_headers()
    headers["Vary"] = "Host"
    return Response(
        content=payload,
        media_type=branding_service.LOGO_MIME,
        headers=headers,
    )


@public_router.get("/branding/logo")
def branding_logo(
    request: Request, db: Session = Depends(get_db)
) -> Response:
    return _serve_asset(request, db, kind=branding_service.ASSET_LOGO)


@public_router.get("/branding/favicon")
def branding_favicon(
    request: Request, db: Session = Depends(get_db)
) -> Response:
    return _serve_asset(request, db, kind=branding_service.ASSET_FAVICON)


__all__ = ["router", "public_router"]