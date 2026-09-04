"""ARCH-26 §3, §4, §5 — warehouse sync and tenant analytics endpoints.

    GET    /organizations/{id}/analytics/destinations              list   [ADMIN]
    POST   /organizations/{id}/analytics/destinations              create [OWNER]
    GET    /organizations/{id}/analytics/destinations/{did}        detail [ADMIN]
    PATCH  /organizations/{id}/analytics/destinations/{did}        update [OWNER]
    DELETE /organizations/{id}/analytics/destinations/{did}        delete [OWNER]
    POST   /organizations/{id}/analytics/destinations/{did}/test   probe  [OWNER]

    GET    /organizations/{id}/analytics/schedules                 list   [ADMIN]
    POST   /organizations/{id}/analytics/schedules                 create [OWNER]
    PATCH  /organizations/{id}/analytics/schedules/{sid}           update [OWNER]
    DELETE /organizations/{id}/analytics/schedules/{sid}           delete [OWNER]

    POST   /organizations/{id}/analytics/sync                      trigger[OWNER]
    GET    /organizations/{id}/analytics/runs                      history[ADMIN]

    GET    /organizations/{id}/analytics/consumption               usage  [ADMIN]
    GET    /organizations/{id}/analytics/datasets                  docs   [ADMIN]

WHY READS ARE ADMIN AND EVERY WRITE IS OWNER
============================================

An administrator has to be able to see which warehouses the tenant syncs to
and why last night's run failed — that is support work, and hiding it behind
OWNER means the person debugging a stale dashboard cannot see the run history
they are debugging.

Every write is OWNER, on the same reasoning ARCH-22 applied to BYOK and
ARCH-25 to custom domains: registering a destination hands a credential for
third-party infrastructure to this platform and starts a recurring egress of
tenant data to it. That is an ownership decision.

`POST .../test` is OWNER despite reading nothing, because it makes an
outbound authenticated request on the tenant's behalf to a host of their
choosing, and the rate at which that can be done is a thing worth restricting
to the smallest role.

WHY THE MANUAL SYNC IS ENQUEUED AND NOT RUN INLINE
==================================================

A push holds a connector call for up to the control-plane timeout, and a
Parquet build for a 90-day window is not a request-cycle operation. The
endpoint returns 202 with the run row already created, so the console has
something to poll immediately rather than a spinner with no identifier behind
it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import (
    OrganizationContext,
    RequireOrgAdmin,
    RequireOrgOwner,
    get_db,
)
from app.core.client_ip import client_ip
from app.models.usage_rollup import UsageRollup
from app.models.warehouse_sync import ExportSchedule, WarehouseDestination
from app.schemas.warehouse_sync import (
    ConnectionTestResult,
    ConsumptionAnalyticsResponse,
    ExportDatasetDescriptor,
    ExportScheduleCreate,
    ExportScheduleResponse,
    ExportScheduleUpdate,
    ExportSyncRunResponse,
    ManualSyncRequest,
    UsageDistributionBucket,
    WarehouseDestinationCreate,
    WarehouseDestinationResponse,
    WarehouseDestinationUpdate,
)
from app.services import job_service
from app.services.analytics import export_engine, sync_service

logger = logging.getLogger("app.api.v1.warehouse_sync")

router = APIRouter(tags=["Analytics & BI Egress"])

BASE = "/organizations/{organization_id}/analytics"

MAX_CONSUMPTION_WINDOW_DAYS = 180


# ---------------------------------------------------------------------------
# Guards and helpers
# ---------------------------------------------------------------------------


def _assert_scope(
    context: OrganizationContext, organization_id: uuid.UUID
) -> None:
    """404 and not 403. A 403 would confirm the organization exists."""
    if context.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )


def _client_context(request: Request) -> dict[str, Optional[str]]:
    """Audit attribution for one request.

    `client_ip` and not `request.client.host`. Behind the ingress the latter
    is the load balancer, identical for every tenant on the cluster, and it
    fills the field with something plausible and wrong. That was ARCH-23
    finding B-1 on the BYOK router; a row recording that a warehouse
    credential was installed is the same class of security-sensitive row.
    """
    return {
        "ip_address": client_ip(request),
        "user_agent": request.headers.get("user-agent"),
    }


def _schedule_response(
    schedule: ExportSchedule, *, destination_label: Optional[str] = None
) -> ExportScheduleResponse:
    """Serialise a schedule with its derived dispatchability.

    `is_dispatchable` is computed here and SENT rather than re-derived in the
    console from `enabled && !circuit_opened_at`. ARCH-24's rule that the
    backend owns a threshold: a control the frontend enables and the server
    then refuses reads as a bug rather than as a policy.
    """
    return ExportScheduleResponse(
        id=schedule.id,
        organization_id=schedule.organization_id,
        destination_id=schedule.destination_id,
        destination_label=destination_label,
        datasets=list(schedule.datasets or []),
        cadence=schedule.cadence,  # type: ignore[arg-type]
        hour_utc=schedule.hour_utc,
        day_of_week=schedule.day_of_week,
        day_of_month=schedule.day_of_month,
        lookback_days=schedule.lookback_days,
        enabled=schedule.enabled,
        consecutive_failure_count=schedule.consecutive_failure_count,
        circuit_opened_at=schedule.circuit_opened_at,
        is_dispatchable=schedule.is_dispatchable,
        last_run_at=schedule.last_run_at,
        next_run_at=schedule.next_run_at,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


def _not_found(what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found."
    )


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------


@router.get(
    BASE + "/destinations", response_model=list[WarehouseDestinationResponse]
)
def list_destinations(
    organization_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgAdmin),
    db: Session = Depends(get_db),
) -> list[WarehouseDestinationResponse]:
    _assert_scope(context, organization_id)
    return [
        WarehouseDestinationResponse.model_validate(row)
        for row in sync_service.list_destinations(
            db, organization_id=organization_id
        )
    ]


@router.post(
    BASE + "/destinations",
    response_model=WarehouseDestinationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_destination(
    organization_id: uuid.UUID,
    payload: WarehouseDestinationCreate,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> WarehouseDestinationResponse:
    _assert_scope(context, organization_id)
    try:
        destination = sync_service.create_destination(
            db,
            organization_id=organization_id,
            label=payload.label,
            credential=payload.credential,
            actor_id=context.user.id,
            **_client_context(request),
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A destination named {payload.label!r} already exists.",
        ) from exc
    except sync_service.SyncServiceError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    db.refresh(destination)
    return WarehouseDestinationResponse.model_validate(destination)


@router.get(
    BASE + "/destinations/{destination_id}",
    response_model=WarehouseDestinationResponse,
)
def get_destination(
    organization_id: uuid.UUID,
    destination_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgAdmin),
    db: Session = Depends(get_db),
) -> WarehouseDestinationResponse:
    _assert_scope(context, organization_id)
    try:
        destination = sync_service.get_destination(
            db, organization_id=organization_id, destination_id=destination_id
        )
    except sync_service.DestinationNotFoundError as exc:
        raise _not_found("Destination") from exc
    return WarehouseDestinationResponse.model_validate(destination)


@router.patch(
    BASE + "/destinations/{destination_id}",
    response_model=WarehouseDestinationResponse,
)
def update_destination(
    organization_id: uuid.UUID,
    destination_id: uuid.UUID,
    payload: WarehouseDestinationUpdate,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> WarehouseDestinationResponse:
    _assert_scope(context, organization_id)
    try:
        destination = sync_service.update_destination(
            db,
            organization_id=organization_id,
            destination_id=destination_id,
            label=payload.label,
            status=payload.status,
            credential=payload.credential,
            actor_id=context.user.id,
            **_client_context(request),
        )
        db.commit()
    except sync_service.DestinationNotFoundError as exc:
        db.rollback()
        raise _not_found("Destination") from exc
    except sync_service.SyncServiceError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    db.refresh(destination)
    return WarehouseDestinationResponse.model_validate(destination)


@router.delete(
    BASE + "/destinations/{destination_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_destination(
    organization_id: uuid.UUID,
    destination_id: uuid.UUID,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> Response:
    _assert_scope(context, organization_id)
    try:
        sync_service.delete_destination(
            db,
            organization_id=organization_id,
            destination_id=destination_id,
            actor_id=context.user.id,
            **_client_context(request),
        )
        db.commit()
    except sync_service.DestinationNotFoundError as exc:
        db.rollback()
        raise _not_found("Destination") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    BASE + "/destinations/{destination_id}/test",
    response_model=ConnectionTestResult,
)
def test_destination(
    organization_id: uuid.UUID,
    destination_id: uuid.UUID,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> ConnectionTestResult:
    """Probe a destination. Returns 200 with `ok: false` on a failed probe.

    Not a 502. The probe ran, we learned the answer, and the answer is the
    payload — a 5xx here would make the console's error handling treat a
    working feature reporting a bad credential as an outage.
    """
    _assert_scope(context, organization_id)
    try:
        outcome = sync_service.test_destination(
            db,
            organization_id=organization_id,
            destination_id=destination_id,
            actor_id=context.user.id,
            **_client_context(request),
        )
        db.commit()
    except sync_service.DestinationNotFoundError as exc:
        db.rollback()
        raise _not_found("Destination") from exc
    return ConnectionTestResult(**outcome)


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


@router.get(BASE + "/schedules", response_model=list[ExportScheduleResponse])
def list_schedules(
    organization_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgAdmin),
    db: Session = Depends(get_db),
) -> list[ExportScheduleResponse]:
    _assert_scope(context, organization_id)
    labels = {
        row.id: row.label
        for row in sync_service.list_destinations(
            db, organization_id=organization_id
        )
    }
    return [
        _schedule_response(
            schedule, destination_label=labels.get(schedule.destination_id)
        )
        for schedule in sync_service.list_schedules(
            db, organization_id=organization_id
        )
    ]


@router.post(
    BASE + "/schedules",
    response_model=ExportScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_schedule(
    organization_id: uuid.UUID,
    payload: ExportScheduleCreate,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> ExportScheduleResponse:
    _assert_scope(context, organization_id)
    try:
        schedule = sync_service.create_schedule(
            db,
            organization_id=organization_id,
            destination_id=payload.destination_id,
            datasets=payload.datasets,
            cadence=payload.cadence,
            hour_utc=payload.hour_utc,
            day_of_week=payload.day_of_week,
            day_of_month=payload.day_of_month,
            lookback_days=payload.lookback_days,
            enabled=payload.enabled,
            actor_id=context.user.id,
            **_client_context(request),
        )
        db.commit()
    except sync_service.DestinationNotFoundError as exc:
        db.rollback()
        raise _not_found("Destination") from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This destination already has a schedule at that cadence. Two "
                "would race and write duplicate parts."
            ),
        ) from exc
    except sync_service.SyncServiceError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    db.refresh(schedule)
    return _schedule_response(schedule)


@router.patch(
    BASE + "/schedules/{schedule_id}", response_model=ExportScheduleResponse
)
def update_schedule(
    organization_id: uuid.UUID,
    schedule_id: uuid.UUID,
    payload: ExportScheduleUpdate,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> ExportScheduleResponse:
    _assert_scope(context, organization_id)
    try:
        schedule = sync_service.update_schedule(
            db,
            organization_id=organization_id,
            schedule_id=schedule_id,
            datasets=payload.datasets,
            cadence=payload.cadence,
            hour_utc=payload.hour_utc,
            day_of_week=payload.day_of_week,
            day_of_month=payload.day_of_month,
            lookback_days=payload.lookback_days,
            enabled=payload.enabled,
            reset_circuit=payload.reset_circuit,
            actor_id=context.user.id,
            **_client_context(request),
        )
        db.commit()
    except sync_service.ScheduleNotFoundError as exc:
        db.rollback()
        raise _not_found("Schedule") from exc
    except sync_service.SyncServiceError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    db.refresh(schedule)
    return _schedule_response(schedule)


@router.delete(
    BASE + "/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_schedule(
    organization_id: uuid.UUID,
    schedule_id: uuid.UUID,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> Response:
    _assert_scope(context, organization_id)
    try:
        sync_service.delete_schedule(
            db,
            organization_id=organization_id,
            schedule_id=schedule_id,
            actor_id=context.user.id,
            **_client_context(request),
        )
        db.commit()
    except sync_service.ScheduleNotFoundError as exc:
        db.rollback()
        raise _not_found("Schedule") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Manual sync and run history
# ---------------------------------------------------------------------------


@router.post(
    BASE + "/sync",
    response_model=dict,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_sync(
    organization_id: uuid.UUID,
    payload: ManualSyncRequest,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Enqueue a run now. 202 with the job id, not a synchronous push."""
    _assert_scope(context, organization_id)
    try:
        destination = sync_service.get_destination(
            db,
            organization_id=organization_id,
            destination_id=payload.destination_id,
        )
    except sync_service.DestinationNotFoundError as exc:
        raise _not_found("Destination") from exc

    if not destination.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This destination is disabled. Enable it before syncing.",
        )

    job = job_service.enqueue(
        db,
        job_type=sync_service.JOB_TYPE_EXPORT_SYNC,
        payload={
            "organization_id": str(organization_id),
            "destination_id": str(destination.id),
            "datasets": list(payload.datasets),
            "lookback_days": int(payload.lookback_days),
            "trigger": "MANUAL",
        },
        organization_id=organization_id,
        max_attempts=1,
    )
    db.commit()

    return {
        "job_id": str(job.id),
        "destination_id": str(destination.id),
        "datasets": list(payload.datasets),
        "status": "QUEUED",
    }


@router.get(BASE + "/runs", response_model=list[ExportSyncRunResponse])
def list_runs(
    organization_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgAdmin),
    db: Session = Depends(get_db),
    limit: int = Query(
        default=sync_service.DEFAULT_RUN_HISTORY_LIMIT,
        ge=1,
        le=sync_service.MAX_RUN_HISTORY_LIMIT,
    ),
    destination_id: Optional[uuid.UUID] = Query(default=None),
) -> list[ExportSyncRunResponse]:
    _assert_scope(context, organization_id)
    runs = sync_service.list_runs(
        db,
        organization_id=organization_id,
        limit=limit,
        destination_id=destination_id,
    )
    return [ExportSyncRunResponse.model_validate(run) for run in runs]


# ---------------------------------------------------------------------------
# Embedded consumption analytics
# ---------------------------------------------------------------------------


@router.get(BASE + "/consumption", response_model=ConsumptionAnalyticsResponse)
def consumption(
    organization_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgAdmin),
    db: Session = Depends(get_db),
    window_days: int = Query(default=30, ge=1, le=MAX_CONSUMPTION_WINDOW_DAYS),
    granularity: str = Query(default="DAY", pattern="^(HOUR|DAY|MONTH)$"),
) -> ConsumptionAnalyticsResponse:
    """The tenant's own consumption, priced at what we invoiced.

    `billed_micros` is `usage_rollups.cost_micros`. The supplier-side column on
    the same row is never selected here — invariant I1, and `verify_arch26.py`
    G5 asserts it by AST over this module as well as the export engine.
    """
    _assert_scope(context, organization_id)

    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=window_days)

    stmt = (
        select(
            UsageRollup.event_type,
            UsageRollup.bucket_start,
            func.sum(UsageRollup.quantity),
            func.sum(UsageRollup.cost_micros),
            func.sum(UsageRollup.event_count),
        )
        .where(UsageRollup.organization_id == organization_id)
        .where(UsageRollup.granularity == granularity)
        .where(UsageRollup.grain == "DETAIL")
        .where(UsageRollup.bucket_start >= window_start)
        .where(UsageRollup.bucket_start < window_end)
        .group_by(UsageRollup.event_type, UsageRollup.bucket_start)
        .order_by(UsageRollup.bucket_start.asc())
        .limit(5000)
    )

    buckets: list[UsageDistributionBucket] = []
    total_billed = 0
    total_events = 0
    for event_type, bucket_start, quantity, billed, events in db.execute(stmt):
        billed_micros = int(billed or 0)
        event_count = int(events or 0)
        buckets.append(
            UsageDistributionBucket(
                event_type=event_type,
                bucket_start=bucket_start,
                quantity=float(quantity or 0),
                billed_micros=billed_micros,
                event_count=event_count,
            )
        )
        total_billed += billed_micros
        total_events += event_count

    # p95 is left None here rather than synthesised. ARCH-17 owns latency
    # percentiles and carries the HISTOGRAM_INTERPOLATED qualifier with them;
    # computing a second, unqualified p95 in this endpoint would put a number
    # next to the SLO console's number with no way to tell which is which.
    return ConsumptionAnalyticsResponse(
        window_start=window_start,
        window_end=window_end,
        granularity=granularity,  # type: ignore[arg-type]
        buckets=buckets,
        total_billed_micros=total_billed,
        total_event_count=total_events,
        p95_latency_ms=None,
        latency_method=None,
    )


@router.get(BASE + "/datasets", response_model=list[ExportDatasetDescriptor])
def list_datasets(
    organization_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> list[ExportDatasetDescriptor]:
    """The versioned column list for every exportable dataset.

    Served from the same specs the writer uses, so a tenant modelling this in
    dbt reads the schema the Parquet actually has rather than a docs page that
    drifted.
    """
    _assert_scope(context, organization_id)
    return [
        ExportDatasetDescriptor(**descriptor)
        for descriptor in export_engine.dataset_descriptors()
    ]


__all__ = ["router"]