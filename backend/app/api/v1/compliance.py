"""ARCH-20 — organization data governance, residency and compliance.

    GET    /organizations/{id}/compliance                     overview
    GET    /organizations/{id}/compliance/residency           current + options
    PUT    /organizations/{id}/compliance/residency           repin        [OWNER]
    GET    /organizations/{id}/compliance/retention           policy
    PUT    /organizations/{id}/compliance/retention           set policy   [OWNER]
    GET    /organizations/{id}/compliance/erasures            tombstones
    GET    /organizations/{id}/compliance/erasures/preview    dry run
    POST   /organizations/{id}/compliance/erasures            erase        [OWNER]
    GET    /organizations/{id}/compliance/exports             list
    POST   /organizations/{id}/compliance/exports             generate
    GET    /organizations/{id}/compliance/exports/{id}/download  mint URL

Reads are ADMIN. The two irreversible writes — repinning residency and erasing
a subject — are OWNER. Retention is OWNER because enabling auto-purge destroys
live rows on a schedule, which is closer to an erasure than to a setting.

Every handler calls _assert_scope first. The role dependency proves the caller
holds a role in SOME organization; it does not prove it is THIS one. Without
the check, an admin of tenant A reaches tenant B's compliance console by
editing the path.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    OrganizationContext,
    RequireOrgAdmin,
    RequireOrgOwner,
    get_db,
)
from app.core.storage import StorageError
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.compliance import (
    PINNED_REGIONS,
    ComplianceExport,
    ErasedSubject,
    RetentionPolicy,
)
from app.models.user import User
from app.schemas.compliance import (
    ComplianceExportDownloadResponse,
    ComplianceExportResponse,
    ComplianceOverviewResponse,
    DataResidencyResponse,
    DataResidencyUpdate,
    ErasedSubjectResponse,
    ErasurePreviewResponse,
    ErasureRequest,
    ErasureResultResponse,
    ResidencyRegionOption,
    RetentionPolicyResponse,
    RetentionPolicyUpdate,
)
from app.services import audit_service
from app.services.compliance import erasure_service, export_service, residency_service

logger = logging.getLogger("app.api.v1.compliance")

router = APIRouter(tags=["Compliance"])

BASE = "/organizations/{organization_id}/compliance"


def _assert_scope(context: OrganizationContext, organization_id: uuid.UUID) -> None:
    if context.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )


def _client_context(request: Request) -> dict[str, str | None]:
    return audit_service.context_from_request(request)


def _residency_payload(context: OrganizationContext) -> DataResidencyResponse:
    configured = set(residency_service.regional_bucket_map().keys())
    options = [
        ResidencyRegionOption(
            region=region,
            # GLOBAL is always available: it is the default bucket, which is
            # what every tenant used before this phase existed.
            configured=(region not in PINNED_REGIONS) or (region in configured),
        )
        for region in residency_service.known_regions()
    ]
    return DataResidencyResponse(
        region=residency_service.region_for_organization(context.organization),
        available_regions=options,
    )


def _policy_payload(
    organization_id: uuid.UUID,
    policy: RetentionPolicy | None,
) -> RetentionPolicyResponse:
    if policy is None:
        return RetentionPolicyResponse(organization_id=organization_id)
    return RetentionPolicyResponse(
        organization_id=organization_id,
        work_item_retention_days=policy.work_item_retention_days,
        audit_retention_days=policy.audit_retention_days,
        conversation_retention_days=policy.conversation_retention_days,
        auto_purge_enabled=policy.auto_purge_enabled,
        updated_at=policy.updated_at,
    )


def _load_policy(db: Session, organization_id: uuid.UUID) -> RetentionPolicy | None:
    return db.execute(
        select(RetentionPolicy).where(
            RetentionPolicy.organization_id == organization_id
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@router.get(
    BASE,
    response_model=ComplianceOverviewResponse,
    summary="Residency, retention and governance activity for this tenant",
)
def get_compliance_overview(
    organization_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> ComplianceOverviewResponse:
    _assert_scope(context, organization_id)

    erasure_count = db.execute(
        select(func.count())
        .select_from(ErasedSubject)
        .where(ErasedSubject.organization_id == organization_id)
    ).scalar_one()

    export_count = db.execute(
        select(func.count())
        .select_from(ComplianceExport)
        .where(ComplianceExport.organization_id == organization_id)
    ).scalar_one()

    latest = db.execute(
        select(ComplianceExport)
        .where(ComplianceExport.organization_id == organization_id)
        .order_by(ComplianceExport.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    return ComplianceOverviewResponse(
        organization_id=organization_id,
        residency=_residency_payload(context),
        retention=_policy_payload(organization_id, _load_policy(db, organization_id)),
        erasure_count=int(erasure_count),
        export_count=int(export_count),
        latest_export=(
            ComplianceExportResponse.model_validate(latest)
            if latest is not None
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Residency
# ---------------------------------------------------------------------------


@router.get(
    f"{BASE}/residency",
    response_model=DataResidencyResponse,
    summary="Current residency region and the regions this deployment serves",
)
def get_residency(
    organization_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> DataResidencyResponse:
    _assert_scope(context, organization_id)
    return _residency_payload(context)


@router.put(
    f"{BASE}/residency",
    response_model=DataResidencyResponse,
    summary="Repin this tenant to a residency region",
)
def update_residency(
    organization_id: uuid.UUID,
    payload: DataResidencyUpdate,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> DataResidencyResponse:
    _assert_scope(context, organization_id)

    try:
        previous = residency_service.set_organization_region(
            db,
            organization=context.organization,
            region=payload.region,
        )
    except residency_service.ResidencyNotConfiguredError as exc:
        # 409, not 400. The request is well-formed and the region is legal;
        # this deployment simply has no bucket there yet. That is a server
        # state problem the operator fixes, not a client mistake.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except residency_service.UnknownRegionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.DATA_RESIDENCY,
        resource_id=organization_id,
        action=AuditAction.RESIDENCY_CHANGED,
        details={"from": previous, "to": payload.region},
        **_client_context(request),
    )
    db.commit()
    db.refresh(context.organization)
    return _residency_payload(context)


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


@router.get(
    f"{BASE}/retention",
    response_model=RetentionPolicyResponse,
    summary="Lifecycle retention policy for this tenant",
)
def get_retention_policy(
    organization_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> RetentionPolicyResponse:
    _assert_scope(context, organization_id)
    return _policy_payload(organization_id, _load_policy(db, organization_id))


@router.put(
    f"{BASE}/retention",
    response_model=RetentionPolicyResponse,
    summary="Set the lifecycle retention policy",
)
def update_retention_policy(
    organization_id: uuid.UUID,
    payload: RetentionPolicyUpdate,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> RetentionPolicyResponse:
    _assert_scope(context, organization_id)

    policy = _load_policy(db, organization_id)
    if policy is None:
        policy = RetentionPolicy(organization_id=organization_id)

    policy.work_item_retention_days = payload.work_item_retention_days
    policy.audit_retention_days = payload.audit_retention_days
    policy.conversation_retention_days = payload.conversation_retention_days
    policy.auto_purge_enabled = payload.auto_purge_enabled
    db.add(policy)
    db.flush()

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.RETENTION_POLICY,
        resource_id=policy.id,
        action=AuditAction.RETENTION_CHANGED,
        details={
            "work_item_retention_days": policy.work_item_retention_days,
            "audit_retention_days": policy.audit_retention_days,
            "conversation_retention_days": policy.conversation_retention_days,
            "auto_purge_enabled": policy.auto_purge_enabled,
        },
        **_client_context(request),
    )
    db.commit()
    db.refresh(policy)
    return _policy_payload(organization_id, policy)


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------


@router.get(
    f"{BASE}/erasures",
    response_model=list[ErasedSubjectResponse],
    summary="Erasure tombstone history",
)
def list_erasures(
    organization_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> list[ErasedSubjectResponse]:
    _assert_scope(context, organization_id)
    rows = erasure_service.list_erasures(
        db, organization_id=organization_id, limit=limit
    )
    return [ErasedSubjectResponse.model_validate(row) for row in rows]


@router.get(
    f"{BASE}/erasures/preview",
    response_model=ErasurePreviewResponse,
    summary="Count what an erasure would destroy, without destroying it",
)
def preview_erasure(
    organization_id: uuid.UUID,
    subject_user_id: uuid.UUID = Query(...),
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> ErasurePreviewResponse:
    _assert_scope(context, organization_id)
    try:
        counts = erasure_service.preview_subject(
            db,
            organization_id=organization_id,
            subject_user_id=subject_user_id,
        )
    except erasure_service.SubjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    return ErasurePreviewResponse(
        subject_user_id=subject_user_id,
        counts=counts,
        preserved_tables=list(erasure_service.PRESERVED_FINANCIAL_TABLES),
    )


@router.post(
    f"{BASE}/erasures",
    response_model=ErasureResultResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Erase a data subject",
)
def create_erasure(
    organization_id: uuid.UUID,
    payload: ErasureRequest,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> ErasureResultResponse:
    _assert_scope(context, organization_id)

    subject = db.get(User, payload.subject_user_id)
    if subject is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That user is not a member of this organization.",
        )

    # The retyped address is checked here rather than in the service so the
    # service stays usable from the CLI, where the operator has already
    # confirmed out of band.
    if subject.email.strip().lower() != payload.confirm_subject_email.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "The confirmation address does not match this user. Erasure "
                "is irreversible, so it is refused rather than guessed at."
            ),
        )

    client = _client_context(request)
    try:
        result = erasure_service.erase_subject(
            db,
            organization=context.organization,
            subject_user_id=payload.subject_user_id,
            erasure_ticket=payload.erasure_ticket,
            actor_user_id=context.user_id,
            ip_address=client.get("ip_address"),
            user_agent=client.get("user_agent"),
        )
    except erasure_service.SubjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except erasure_service.SubjectProtectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    db.commit()
    db.refresh(result.erased_subject)

    return ErasureResultResponse(
        erased_subject=ErasedSubjectResponse.model_validate(result.erased_subject),
        already_erased=result.already_erased,
        counts=result.counts,
    )


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


@router.get(
    f"{BASE}/exports",
    response_model=list[ComplianceExportResponse],
    summary="DPA export bundles",
)
def list_exports(
    organization_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> list[ComplianceExportResponse]:
    _assert_scope(context, organization_id)
    rows = export_service.list_exports(
        db, organization_id=organization_id, limit=limit
    )
    return [ComplianceExportResponse.model_validate(row) for row in rows]


@router.post(
    f"{BASE}/exports",
    response_model=ComplianceExportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a DPA export bundle",
)
def create_export(
    organization_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> ComplianceExportResponse:
    _assert_scope(context, organization_id)

    client = _client_context(request)
    record = export_service.request_export(
        db,
        organization=context.organization,
        requested_by_user_id=context.user_id,
        ip_address=client.get("ip_address"),
        user_agent=client.get("user_agent"),
    )
    # Generated inline and bounded by MAX_ROWS_PER_SECTION. A job would be the
    # right shape at a larger scale; a job that writes a row a caller is about
    # to poll for is the wrong shape at this one.
    record = export_service.generate_export(
        db, organization=context.organization, export=record
    )
    db.commit()
    db.refresh(record)
    return ComplianceExportResponse.model_validate(record)


@router.get(
    f"{BASE}/exports/{{export_id}}/download",
    response_model=ComplianceExportDownloadResponse,
    summary="Mint a short-lived download URL for a completed bundle",
)
def download_export(
    organization_id: uuid.UUID,
    export_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> ComplianceExportDownloadResponse:
    _assert_scope(context, organization_id)

    record = db.execute(
        select(ComplianceExport).where(
            ComplianceExport.id == export_id,
            ComplianceExport.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Export not found."
        )

    try:
        url = export_service.download_url_for(context.organization, record)
    except export_service.ExportNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.COMPLIANCE_EXPORT,
        resource_id=record.id,
        action=AuditAction.ACCESSED,
        details={"residency_region": record.residency_region},
        **_client_context(request),
    )
    db.commit()

    return ComplianceExportDownloadResponse(
        export_id=record.id,
        download_url=url,
        expires_in_seconds=export_service.DOWNLOAD_URL_TTL_SECONDS,
    )