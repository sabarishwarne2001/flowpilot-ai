"""PHASE 4 — global search (Ctrl+K) across the caller's workspaces.

    GET /organizations/{organization_id}/search?q=...&limit=...   [any member]

Documents, entities and cases from every workspace of the organization the
caller may open (app/services/global_search_service.py), each with the
workspace it lives in so the console can open it there.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api import deps
from app.services import global_search_service as service

router = APIRouter(tags=["Search"])


class SearchHitOut(BaseModel):
    kind: str
    id: uuid.UUID
    title: str
    subtitle: Optional[str] = None
    workspace_id: uuid.UUID
    workspace_slug: str
    workspace_name: str


class SearchResponse(BaseModel):
    query: str
    workspaces_searched: int
    documents: list[SearchHitOut]
    entities: list[SearchHitOut]
    cases: list[SearchHitOut]


@router.get(
    "/organizations/{organization_id}/search",
    response_model=SearchResponse,
    summary="Search documents, entities and cases across your workspaces",
)
def global_search(
    organization_id: uuid.UUID,
    db: deps.DbSession,
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(service.DEFAULT_LIMIT, ge=1, le=service.MAX_LIMIT),
    context: deps.OrganizationContext = Depends(deps.RequireOrgMember),
) -> SearchResponse:
    if context.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    try:
        results = service.search(
            db,
            organization=context.organization,
            user_id=context.user_id,
            organization_role=context.role,
            query=q,
            limit=limit,
        )
    except service.SearchQueryError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return SearchResponse(
        query=results.query,
        workspaces_searched=results.workspaces_searched,
        documents=[SearchHitOut(**vars(h)) for h in results.documents],
        entities=[SearchHitOut(**vars(h)) for h in results.entities],
        cases=[SearchHitOut(**vars(h)) for h in results.cases],
    )
