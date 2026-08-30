"""
ARCH-17 — per-tenant SLO targets and compliance.

    GET /organizations/{organization_id}/slos            effective targets + compliance
    PUT /organizations/{organization_id}/slos/{slo_key}  set a tenant override
    DELETE /organizations/{organization_id}/slos/{slo_key} drop override
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import OrganizationContext, RequireOrgAdmin, get_db
from app.core.slo_registry import SLORegistryError, SLO_REGISTRY
from app.models.slo import SLOWindow
from app.schemas.slo import (
    SLOComplianceEntry,
    SLOSummaryResponse,
    SLOTarget,
    SLOTargetUpdate,
)
from app.services import slo_service

logger = logging.getLogger("app.api.v1.slos")

router = APIRouter(tags=["SLOs"])


def _assert_scope(context: OrganizationContext, organization_id: uuid.UUID) -> None:
    if context.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )


def _target_of(effective) -> SLOTarget:
    return SLOTarget(
        slo_key=effective.slo_key,
        display_name=effective.display_name,
        description=effective.description,
        unit=effective.unit,
        target_value=effective.target_value,
        window_period=effective.window_period,
        is_contractual=effective.is_contractual,
        source=effective.source,
        definition_id=effective.definition_id,
        notes=effective.notes,
    )


@router.get(
    "/organizations/{organization_id}/slos",
    response_model=SLOSummaryResponse,
    summary="Effective SLO targets and current compliance",
)
def list_organization_slos(
    organization_id: uuid.UUID,
    period: SLOWindow = Query(
        SLOWindow.DAY,
        description=(
            "Trailing history granularity: HOUR looks back 24h, DAY 30d, "
            "MONTH 365d. Does not change each SLO's own measurement window."
        ),
    ),
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> SLOSummaryResponse:
    _assert_scope(context, organization_id)

    summary = slo_service.get_tenant_slo_summary(
        db, organization_id=context.organization_id, period=period
    )

    return SLOSummaryResponse(
        organization_id=summary.organization_id,
        as_of=summary.as_of,
        period=summary.period,
        contractual_breaches=summary.contractual_breaches,
        entries=[
            SLOComplianceEntry(
                slo_key=entry.effective.slo_key,
                target=_target_of(entry.effective),
                observed_value=entry.observed_value,
                sample_count=entry.sample_count,
                error_count=entry.error_count,
                breached=entry.breached,
                method=entry.method,
                window_start=entry.window_start,
                window_end=entry.window_end,
                breached_windows=entry.breached_windows,
                total_windows=entry.total_windows,
                compliance_ratio=entry.compliance_ratio,
            )
            for entry in summary.entries
        ],
    )


@router.put(
    "/organizations/{organization_id}/slos/{slo_key}",
    response_model=SLOTarget,
    summary="Set a tenant SLO target",
)
def set_organization_slo(
    organization_id: uuid.UUID,
    slo_key: str,
    payload: SLOTargetUpdate,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> SLOTarget:
    _assert_scope(context, organization_id)

    try:
        definition = slo_service.set_target(
            db,
            organization_id=organization_id,
            slo_key=slo_key,
            target_value=payload.target_value,
            window_period=payload.window_period,
            is_contractual=payload.is_contractual,
            notes=payload.notes,
        )
    except SLORegistryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except slo_service.SLOServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    db.commit()
    db.refresh(definition)

    spec = SLO_REGISTRY[slo_key]
    return SLOTarget(
        slo_key=definition.slo_key,
        display_name=definition.display_name or spec.display_name,
        description=spec.description,
        unit=definition.unit,
        target_value=definition.target_value,
        window_period=definition.window_period,
        is_contractual=definition.is_contractual,
        source="ORGANIZATION",
        definition_id=definition.id,
        notes=definition.notes,
    )


@router.delete(
    "/organizations/{organization_id}/slos/{slo_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Drop a tenant override and fall back to the platform default",
)
def delete_organization_slo(
    organization_id: uuid.UUID,
    slo_key: str,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> Response:
    _assert_scope(context, organization_id)

    removed = slo_service.delete_target(
        db, organization_id=organization_id, slo_key=slo_key
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No tenant override exists for that SLO.",
        )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]