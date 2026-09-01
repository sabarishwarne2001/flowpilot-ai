"""ARCH-21 §3.1 — the public developer gateway.

    GET  /api/v1/public                             version and policy
    GET  /api/v1/public/documents                   list documents
    GET  /api/v1/public/documents/{work_item_id}    one document
    POST /api/v1/public/query                       hybrid retrieval
    GET  /api/v1/public/workflows                   list automation rules
    POST /api/v1/public/workflows/{rule_id}/trigger raise an event

FOUR THINGS THAT ARE NOT OBVIOUS FROM THE ROUTE TABLE
=====================================================

**Authentication is `require_api_key`, not `get_current_user`.** A browser
session must not reach the commercial gateway: it would bypass every per-key
limit and meter as an unattributed request. The dependency name is also load-
bearing — `app/main.py::assert_public_route_registry` raises at startup for
any route whose dependency tree carries no name from `_AUTH_DEPENDENCY_NAMES`
and which is absent from `PUBLIC_ROUTES`.

**Authorisation is a second, separate step.** `require_api_key` proves the
credential; `_require_scope` proves the grant. `RequireScope` is not usable
here because it depends on `get_organization_context`, which resolves an
organization from a path parameter these routes do not have — the organization
comes from the key. So the scope check reads the same `effective_scopes()` the
console path does, against the same `ROUTE_SCOPE_MAP` entries, and refuses the
same way.

**Metering commits with the work.** `_meter` runs inside the handler's
transaction, not in middleware. A response that was served but not metered is
revenue leakage; one metered but not served is a billing dispute. `db.commit()`
at the end of each handler is what makes them atomic.

**Deprecation headers are emitted from one place.** RFC 8594 `Sunset`, the
`Deprecation` header, and an RFC 8288 `Link` to the successor are attached by
`_apply_version_headers` on every response. Today the values are empty because
v1 is current; when v2 ships, one constant changes and every route starts
advertising it. Scattering that across six handlers is how a public API ends
up announcing its own sunset on four routes out of six.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import PublicApiPrincipal, get_db, require_api_key
from app.core.config import settings
from app.core.scopes import PUBLIC_API_SCOPES, ApiKeyScope, effective_scopes
from app.schemas.public_api import (
    PublicApiVersion,
    PublicDocument,
    PublicDocumentPage,
    PublicDocumentResponse,
    PublicQueryRequest,
    PublicQueryResponse,
    PublicQueryResult,
    PublicWorkflow,
    PublicWorkflowList,
    PublicWorkflowTriggerRequest,
    PublicWorkflowTriggerResponse,
    RateLimitSnapshot,
)
from app.services import public_api_service

logger = logging.getLogger("app.api.v1.public.gateway")

router = APIRouter(prefix="/public", tags=["Public API"])

#: Contract version served by this module.
API_VERSION: str = "2026-09-01"
API_STATUS: str = "STABLE"

#: RFC 8594 / draft-deprecation. Both None while v1 is current. Setting them
#: is the ONLY edit needed to begin advertising a sunset across every route.
DEPRECATION_AT: Optional[datetime] = None
SUNSET_AT: Optional[datetime] = None
SUCCESSOR_URL: Optional[str] = None
DOCUMENTATION_URL: str = "https://docs.flowpilot.ai/public-api"

HEADER_API_VERSION = "X-FlowPilot-API-Version"


def _apply_version_headers(response: Response) -> None:
    """Contract version, plus deprecation signalling when it applies."""
    response.headers[HEADER_API_VERSION] = API_VERSION
    response.headers["Link"] = f'<{DOCUMENTATION_URL}>; rel="describedby"'

    if DEPRECATION_AT is not None:
        # The Deprecation header carries an HTTP-date, not an ISO timestamp.
        response.headers["Deprecation"] = DEPRECATION_AT.strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
    if SUNSET_AT is not None:
        response.headers["Sunset"] = SUNSET_AT.strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
    if SUCCESSOR_URL is not None:
        response.headers["Link"] = (
            f'<{DOCUMENTATION_URL}>; rel="describedby", '
            f'<{SUCCESSOR_URL}>; rel="successor-version"'
        )


def _snapshot(request: Request) -> RateLimitSnapshot:
    state: dict[str, Any] = getattr(request.state, "public_rate_limit", {}) or {}
    return RateLimitSnapshot(
        tier=str(state.get("tier", "FREE")),
        limit=int(state.get("limit", 0)),
        remaining=max(0, int(state.get("remaining", 0))),
        reset_seconds=int(state.get("reset_seconds", 0)),
    )


def _require_scope(
    principal: PublicApiPrincipal, required: ApiKeyScope
) -> None:
    """Deny-by-default scope enforcement for the gateway.

    Reads `effective_scopes()`, which intersects what the key was granted
    with what the ISSUER's current organization role still permits. That
    second half is why a demoted issuer's key loses reach without anyone
    having to remember to revoke it.
    """
    granted = effective_scopes(principal.api_key, principal.membership)
    if required not in granted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This API key lacks the required scope: {required.value}",
        )


def _meter(
    db: Session,
    *,
    principal: PublicApiPrincipal,
    request: Request,
    status_code: int,
    started: float,
    workspace_id: Optional[uuid.UUID] = None,
) -> None:
    latency_ms = (time.perf_counter() - started) * 1000.0
    public_api_service.meter_request(
        db,
        key=principal.api_key,
        route=request.url.path,
        method=request.method,
        status_code=status_code,
        latency_ms=latency_ms,
        workspace_id=workspace_id,
    )


def _translate(exc: public_api_service.PublicApiError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


# ===========================================================================
# Version
# ===========================================================================


@router.get(
    "",
    response_model=PublicApiVersion,
    summary="Public API version and policy",
)
def api_version(
    response: Response,
    principal: PublicApiPrincipal = Depends(require_api_key),
) -> Any:
    """Contract metadata for the authenticated key.

    Authenticated rather than anonymous on purpose. An unauthenticated
    version endpoint is a free reconnaissance surface, and the interesting
    half of this payload — which scopes THIS key actually holds — requires a
    principal anyway.
    """
    _apply_version_headers(response)
    granted = effective_scopes(principal.api_key, principal.membership)
    return PublicApiVersion(
        version=API_VERSION,
        status=API_STATUS,
        deprecation=DEPRECATION_AT,
        sunset=SUNSET_AT,
        documentation_url=DOCUMENTATION_URL,
        supported_scopes=sorted(
            scope.value for scope in granted if scope in PUBLIC_API_SCOPES
        ),
    )


# ===========================================================================
# Documents
# ===========================================================================


@router.get(
    "/documents",
    response_model=PublicDocumentPage,
    summary="List documents in a workspace",
)
def list_documents(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    principal: PublicApiPrincipal = Depends(require_api_key),
    workspace_id: uuid.UUID = Query(
        ..., description="Must belong to the key's organization."
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(
        public_api_service.DEFAULT_PAGE_SIZE,
        ge=1,
        le=public_api_service.MAX_PAGE_SIZE,
    ),
    document_status: Optional[str] = Query(None, alias="status"),
) -> Any:
    started = time.perf_counter()
    _apply_version_headers(response)
    _require_scope(principal, ApiKeyScope.PUBLIC_DOCUMENTS_READ)

    try:
        result = public_api_service.list_documents(
            db,
            organization_id=principal.organization_id,
            workspace_id=workspace_id,
            page=page,
            page_size=page_size,
            status=document_status,
        )
    except public_api_service.PublicApiError as exc:
        _meter(
            db,
            principal=principal,
            request=request,
            status_code=exc.status_code,
            started=started,
        )
        db.commit()
        raise _translate(exc) from exc

    _meter(
        db,
        principal=principal,
        request=request,
        status_code=200,
        started=started,
        workspace_id=workspace_id,
    )
    db.commit()

    return PublicDocumentPage(
        items=[PublicDocument(**item) for item in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        rate_limit=_snapshot(request),
    )


@router.get(
    "/documents/{work_item_id}",
    response_model=PublicDocumentResponse,
    summary="Get one document",
)
def get_document(
    request: Request,
    response: Response,
    work_item_id: uuid.UUID = Path(...),
    db: Session = Depends(get_db),
    principal: PublicApiPrincipal = Depends(require_api_key),
    workspace_id: uuid.UUID = Query(...),
) -> Any:
    started = time.perf_counter()
    _apply_version_headers(response)
    _require_scope(principal, ApiKeyScope.PUBLIC_DOCUMENTS_READ)

    try:
        document = public_api_service.get_document(
            db,
            organization_id=principal.organization_id,
            workspace_id=workspace_id,
            work_item_id=work_item_id,
        )
    except public_api_service.PublicApiError as exc:
        _meter(
            db,
            principal=principal,
            request=request,
            status_code=exc.status_code,
            started=started,
        )
        db.commit()
        raise _translate(exc) from exc

    _meter(
        db,
        principal=principal,
        request=request,
        status_code=200,
        started=started,
        workspace_id=workspace_id,
    )
    db.commit()

    return PublicDocumentResponse(
        document=PublicDocument(**document),
        rate_limit=_snapshot(request),
    )


# ===========================================================================
# Query
# ===========================================================================


@router.post(
    "/query",
    response_model=PublicQueryResponse,
    summary="Hybrid retrieval over a workspace",
)
def run_query(
    payload: PublicQueryRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    principal: PublicApiPrincipal = Depends(require_api_key),
) -> Any:
    started = time.perf_counter()
    _apply_version_headers(response)
    _require_scope(principal, ApiKeyScope.PUBLIC_QUERY_WRITE)

    try:
        outcome = public_api_service.run_query(
            db,
            organization_id=principal.organization_id,
            workspace_id=payload.workspace_id,
            query=payload.query,
            tier=principal.tier,
            top_k=payload.top_k,
            work_item_ids=payload.work_item_ids,
        )
    except public_api_service.PublicApiError as exc:
        _meter(
            db,
            principal=principal,
            request=request,
            status_code=exc.status_code,
            started=started,
        )
        db.commit()
        raise _translate(exc) from exc

    _meter(
        db,
        principal=principal,
        request=request,
        status_code=200,
        started=started,
        workspace_id=payload.workspace_id,
    )
    db.commit()

    return PublicQueryResponse(
        results=[
            PublicQueryResult(
                id=str(row.get("id")),
                text=str(row.get("text", "")),
                document_name=str(row.get("document_name", "")),
                work_item_id=str(row.get("work_item_id", "")),
                chunk_index=row.get("chunk_index"),
                page_number=row.get("page_number"),
                similarity_score=row.get("similarity_score"),
            )
            for row in outcome.results
        ],
        result_count=len(outcome.results),
        latency_ms=round(outcome.latency_ms, 3),
        tier=outcome.tier,
        ef_search=outcome.ef_search_applied,
        retrieval_arms=outcome.arms,
        rate_limit=_snapshot(request),
    )


# ===========================================================================
# Workflows
# ===========================================================================


@router.get(
    "/workflows",
    response_model=PublicWorkflowList,
    summary="List automation rules in a workspace",
)
def list_workflows(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    principal: PublicApiPrincipal = Depends(require_api_key),
    workspace_id: uuid.UUID = Query(...),
    active_only: bool = Query(True),
) -> Any:
    started = time.perf_counter()
    _apply_version_headers(response)
    _require_scope(principal, ApiKeyScope.PUBLIC_WORKFLOWS_READ)

    try:
        rows = public_api_service.list_workflows(
            db,
            organization_id=principal.organization_id,
            workspace_id=workspace_id,
            active_only=active_only,
        )
    except public_api_service.PublicApiError as exc:
        _meter(
            db,
            principal=principal,
            request=request,
            status_code=exc.status_code,
            started=started,
        )
        db.commit()
        raise _translate(exc) from exc

    _meter(
        db,
        principal=principal,
        request=request,
        status_code=200,
        started=started,
        workspace_id=workspace_id,
    )
    db.commit()

    return PublicWorkflowList(
        items=[PublicWorkflow(**row) for row in rows],
        rate_limit=_snapshot(request),
    )


@router.post(
    "/workflows/{rule_id}/trigger",
    response_model=PublicWorkflowTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Raise an automation event for a document",
)
def trigger_workflow(
    payload: PublicWorkflowTriggerRequest,
    request: Request,
    response: Response,
    rule_id: uuid.UUID = Path(...),
    db: Session = Depends(get_db),
    principal: PublicApiPrincipal = Depends(require_api_key),
) -> Any:
    """202, not 200, and the difference is the contract.

    Nothing has run when this returns. An event has been committed to the
    outbox and the automation engine will resolve which rules match it. A 200
    would tell a developer their rule executed, and they would build retry
    logic on that belief.
    """
    started = time.perf_counter()
    _apply_version_headers(response)
    _require_scope(principal, ApiKeyScope.PUBLIC_WORKFLOWS_WRITE)

    if not db.in_transaction():
        db.begin()

    try:
        result = public_api_service.trigger_workflow(
            db,
            organization_id=principal.organization_id,
            workspace_id=payload.workspace_id,
            rule_id=rule_id,
            work_item_id=payload.work_item_id,
            key=principal.api_key,
        )
    except public_api_service.PublicApiError as exc:
        db.rollback()
        _meter(
            db,
            principal=principal,
            request=request,
            status_code=exc.status_code,
            started=started,
        )
        db.commit()
        raise _translate(exc) from exc

    _meter(
        db,
        principal=principal,
        request=request,
        status_code=202,
        started=started,
        workspace_id=payload.workspace_id,
    )
    db.commit()

    return PublicWorkflowTriggerResponse(
        outbox_event_id=result["outbox_event_id"],
        rule_id=result["rule_id"],
        work_item_id=result["work_item_id"],
        status=result["status"],
        note=result["note"],
        rate_limit=_snapshot(request),
    )


__all__ = [
    "API_STATUS",
    "API_VERSION",
    "DEPRECATION_AT",
    "DOCUMENTATION_URL",
    "HEADER_API_VERSION",
    "SUNSET_AT",
    "router",
]