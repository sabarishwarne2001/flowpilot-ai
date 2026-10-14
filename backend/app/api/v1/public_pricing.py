"""The public price list behind the `/pricing` page (campaign session 4, CAMPAIGN-MAP 4.3).

    GET /api/v1/pricing

No session and no tenant: it answers the same thing to everyone, so it is
registered in `app.core.public_route_registry.PUBLIC_ROUTES` with the public
read rate limit, and it may be cached for five minutes (the tier cache in
`quota_service` holds a published version that long anyway).

It lives outside `/api/v1/public/...` on purpose: that prefix is the API-key
developer gateway, and `tests/api/test_public_api_endpoints.py` keeps every
unauthenticated route out of it (owner decision N-024).

The page must show what is sold, so nothing is typed into the page: the plans,
prices and allowances are read from the published tier versions and plan price
books by `app.services.plan_catalog`, the same helpers the in-app plan cards
(`GET .../billing/plans`) use.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.public_pricing import PublicPricingResponse
from app.services import plan_catalog

router = APIRouter(tags=["Pricing (public)"])

#: Shared caches may keep the list for five minutes; it holds nothing about the caller.
CACHE_CONTROL = "public, max-age=300"


@router.get(
    "/pricing",
    response_model=PublicPricingResponse,
    summary="The published plans, their prices per seat and the free trial (no sign-in)",
)
def public_pricing(response: Response, db: Session = Depends(get_db)) -> Any:
    response.headers["Cache-Control"] = CACHE_CONTROL
    return plan_catalog.public_catalog(db)


__all__ = ["CACHE_CONTROL", "router"]
