"""ARCH-27 §3 — the tenant-facing marketplace.

    GET    /organizations/{oid}/marketplace/catalog                 [ADMIN]
    GET    /organizations/{oid}/marketplace/manifests/{mid}         [ADMIN]
    GET    /organizations/{oid}/marketplace/installations           [ADMIN]
    POST   /organizations/{oid}/marketplace/installations           [OWNER]
    DELETE /organizations/{oid}/marketplace/installations/{iid}     [OWNER]
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    OrganizationContext,
    RequireOrgAdmin,
    RequireOrgOwner,
    get_db,
)
from app.core.client_ip import client_ip
from app.models.partner import (
    MarketplaceManifest,
    MarketplaceSignature,
    PartnerSigningKey,
)
from app.schemas.partner import (
    InstallationCreate,
    InstallationResponse,
    ManifestDetailResponse,
    ManifestEdgeSubmission,
    ManifestNodeSubmission,
    ManifestResponse,
    ManifestSignatureResponse,
    MarketplaceItemResponse,
)
from app.services.partner import marketplace_service
from app.services.partner.tenancy_service import PartnerError

logger = logging.getLogger("app.api.v1.marketplace")

router = APIRouter(
    prefix="/organizations/{organization_id}/marketplace",
    tags=["Partner Marketplace"],
)


def _http(exc: PartnerError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _signature_rows(
    db: Session, *, manifest_id: uuid.UUID
) -> list[dict[str, Any]]:
    joined = db.execute(
        select(MarketplaceSignature, PartnerSigningKey)
        .join(
            PartnerSigningKey,
            PartnerSigningKey.id == MarketplaceSignature.signing_key_id,
        )
        .where(MarketplaceSignature.manifest_id == manifest_id)
        .order_by(MarketplaceSignature.created_at)
    ).all()

    return [
        {
            "id": signature.id,
            "algorithm": signature.algorithm,
            "signed_digest": signature.signed_digest,
            "verified_at": signature.verified_at,
            "signing_key_fingerprint": key.fingerprint,
            "signing_key_status": key.status,
        }
        for signature, key in joined
    ]


@router.get("/catalog", response_model=list[MarketplaceItemResponse])
def browse_catalog(
    organization_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgAdmin),
    category: Optional[str] = Query(default=None, max_length=32),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """PUBLIC items plus this tenant's own partner's PARTNER_ONLY items."""
    return marketplace_service.catalog_for_organization(
        db, organization_id=context.organization_id, category=category
    )


@router.get(
    "/manifests/{manifest_id}", response_model=ManifestDetailResponse
)
def inspect_manifest(
    organization_id: uuid.UUID,
    manifest_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgAdmin),
    db: Session = Depends(get_db),
) -> ManifestDetailResponse:
    """The DAG body plus a live signature verdict, for pre-install review."""
    try:
        manifest, item = marketplace_service.manifest_for_organization(
            db,
            organization_id=context.organization_id,
            manifest_id=manifest_id,
        )
    except PartnerError as exc:
        raise _http(exc) from exc

    verified, _signature, key = marketplace_service.verify_manifest_signature(
        db, manifest=manifest
    )

    body = manifest.manifest or {}
    nodes = [
        ManifestNodeSubmission(
            node_key=str(entry.get("node_key")),
            node_type=str(entry.get("node_type")),
            config=dict(entry.get("config") or {}),
        )
        for entry in (body.get("nodes") or [])
    ]
    edges = [
        ManifestEdgeSubmission(
            from_node_key=str(entry.get("from_node_key")),
            to_node_key=str(entry.get("to_node_key")),
            branch=str(entry.get("branch") or "default"),
        )
        for entry in (body.get("edges") or [])
    ]

    return ManifestDetailResponse(
        manifest=ManifestResponse(
            id=manifest.id,
            item_id=manifest.item_id,
            version=manifest.version,
            status=manifest.status,
            content_digest=manifest.content_digest,
            node_count=manifest.node_count,
            edge_count=manifest.edge_count,
            published_at=manifest.published_at,
            signatures=[
                ManifestSignatureResponse(**row)
                for row in _signature_rows(db, manifest_id=manifest.id)
            ],
        ),
        nodes=nodes,
        edges=edges,
        signature_verified=verified,
        verified_key_fingerprint=(key.fingerprint if key is not None else None),
    )


@router.get("/installations", response_model=list[InstallationResponse])
def list_installations(
    organization_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgAdmin),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return marketplace_service.installations_for(
        db, organization_id=context.organization_id
    )


@router.post(
    "/installations",
    response_model=InstallationResponse,
    status_code=status.HTTP_201_CREATED,
)
def install(
    organization_id: uuid.UUID,
    payload: InstallationCreate,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Admit a signed manifest. OWNER-gated; invariants 5 and 6 re-checked."""
    try:
        installation = marketplace_service.install_manifest(
            db,
            organization_id=context.organization_id,
            workspace_id=payload.workspace_id,
            manifest_id=payload.manifest_id,
            rule_name=payload.rule_name,
            enabled=payload.enabled,
            actor_id=context.user_id,
            ip_address=client_ip(request),
        )
    except PartnerError as exc:
        db.commit()
        raise _http(exc) from exc
    db.commit()

    for entry in marketplace_service.installations_for(
        db, organization_id=context.organization_id
    ):
        if entry["id"] == installation.id:
            return entry
    raise HTTPException(status_code=500, detail="Installation did not persist.")


@router.delete(
    "/installations/{installation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def uninstall(
    organization_id: uuid.UUID,
    installation_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> Response:
    try:
        marketplace_service.remove_installation(
            db,
            organization_id=context.organization_id,
            installation_id=installation_id,
            actor_id=context.user_id,
        )
    except PartnerError as exc:
        raise _http(exc) from exc
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)