"""ARCH-20 — tenant DPA export bundles."""

from __future__ import annotations

import io
import json
import logging
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.compliance import (
    EXPORT_COMPLETE,
    EXPORT_EXPIRED,
    EXPORT_FAILED,
    EXPORT_PENDING,
    EXPORT_RUNNING,
    ComplianceExport,
    ErasedSubject,
    RetentionPolicy,
)
from app.models.organization import Organization
from app.services import audit_service
from app.services.compliance import residency_service

logger = logging.getLogger("app.services.compliance.export")

__all__ = [
    "ExportError",
    "ExportNotReadyError",
    "build_bundle",
    "download_url_for",
    "expire_stale_exports",
    "generate_export",
    "list_exports",
    "request_export",
]

BUNDLE_VERSION: str = "arch20.1"
MAX_ROWS_PER_SECTION: int = 50_000
DEFAULT_TTL_HOURS: int = 72
DOWNLOAD_URL_TTL_SECONDS: int = 900


class ExportError(RuntimeError):
    pass


class ExportNotReadyError(ExportError):
    pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def _rows(db: Session, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    result = db.execute(text(sql), params)
    keys = list(result.keys())
    return [
        {key: _jsonable(value) for key, value in zip(keys, row)}
        for row in result.fetchall()
    ]


def _ttl_hours() -> int:
    return int(getattr(settings, "COMPLIANCE_EXPORT_TTL_HOURS", DEFAULT_TTL_HOURS))


def build_bundle(
    db: Session,
    *,
    organization: Organization,
) -> dict[str, Any]:
    organization_id = organization.id
    limit = MAX_ROWS_PER_SECTION

    members = _rows(
        db,
        """
        SELECT om.id,
               om.user_id,
               u.email,
               u.display_name,
               om.role::text  AS role,
               om.status::text AS status,
               om.created_at
        FROM organization_members om
        JOIN users u ON u.id = om.user_id
        WHERE om.organization_id = :org
        ORDER BY om.created_at
        LIMIT :lim
        """,
        {"org": organization_id, "lim": limit},
    )

    workspaces = _rows(
        db,
        """
        SELECT id, slug, workspace_name, status::text AS status, created_at
        FROM workspaces
        WHERE organization_id = :org
        ORDER BY created_at
        LIMIT :lim
        """,
        {"org": organization_id, "lim": limit},
    )

    work_items = _rows(
        db,
        """
        SELECT wi.id,
               wi.workspace_id,
               wi.original_filename,
               wi.file_type,
               wi.file_size,
               wi.status,
               wi.pipeline_stage::text AS pipeline_stage,
               wi.page_count,
               wi.created_by_user_id,
               wi.created_at
        FROM work_items wi
        JOIN workspaces w ON w.id = wi.workspace_id
        WHERE w.organization_id = :org
        ORDER BY wi.created_at
        LIMIT :lim
        """,
        {"org": organization_id, "lim": limit},
    )

    conversations = _rows(
        db,
        """
        SELECT c.id,
               c.workspace_id,
               c.user_id,
               c.title,
               c.work_item_id,
               c.created_at
        FROM conversations c
        JOIN workspaces w ON w.id = c.workspace_id
        WHERE w.organization_id = :org
        ORDER BY c.created_at
        LIMIT :lim
        """,
        {"org": organization_id, "lim": limit},
    )

    messages = _rows(
        db,
        """
        SELECT m.id,
               m.conversation_id,
               m.role,
               m.content,
               m.created_at
        FROM conversation_messages m
        JOIN conversations c ON c.id = m.conversation_id
        JOIN workspaces w ON w.id = c.workspace_id
        WHERE w.organization_id = :org
        ORDER BY m.created_at
        LIMIT :lim
        """,
        {"org": organization_id, "lim": limit},
    )

    audit_logs = _rows(
        db,
        """
        SELECT id,
               actor_id,
               api_key_id,
               resource_type::text AS resource_type,
               resource_id,
               action::text AS action,
               outcome::text AS outcome,
               ip_address,
               created_at
        FROM audit_logs
        WHERE organization_id = :org
        ORDER BY created_at DESC
        LIMIT :lim
        """,
        {"org": organization_id, "lim": limit},
    )

    invoices = _rows(
        db,
        """
        SELECT i.id,
               i.number,
               i.status::text AS status,
               i.currency,
               i.created_at
        FROM invoices i
        JOIN billing_accounts b ON b.id = i.billing_account_id
        WHERE b.organization_id = :org
        ORDER BY i.created_at
        LIMIT :lim
        """,
        {"org": organization_id, "lim": limit},
    )

    erasures = [
        {
            "id": str(row.id),
            "subject_user_id": str(row.subject_user_id)
            if row.subject_user_id
            else None,
            "subject_email_hash": row.subject_email_hash,
            "erasure_ticket": row.erasure_ticket,
            "erased_at": row.erased_at.isoformat() if row.erased_at else None,
            "details": _jsonable(row.details),
        }
        for row in db.execute(
            select(ErasedSubject)
            .where(ErasedSubject.organization_id == organization_id)
            .order_by(ErasedSubject.erased_at.desc())
        )
        .scalars()
        .all()
    ]

    policy = db.execute(
        select(RetentionPolicy).where(
            RetentionPolicy.organization_id == organization_id
        )
    ).scalar_one_or_none()

    return {
        "bundle_version": BUNDLE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "organization": {
            "id": str(organization.id),
            "slug": organization.slug,
            "name": organization.name,
            "legal_name": organization.legal_name,
            "status": getattr(organization.status, "value", organization.status),
            "data_residency_region": residency_service.region_for_organization(
                organization
            ),
        },
        "retention_policy": (
            {
                "work_item_retention_days": policy.work_item_retention_days,
                "audit_retention_days": policy.audit_retention_days,
                "conversation_retention_days": policy.conversation_retention_days,
                "auto_purge_enabled": policy.auto_purge_enabled,
            }
            if policy is not None
            else None
        ),
        "row_limit_per_section": MAX_ROWS_PER_SECTION,
        "sections": {
            "members": members,
            "workspaces": workspaces,
            "work_items": work_items,
            "conversations": conversations,
            "conversation_messages": messages,
            "audit_logs": audit_logs,
            "invoices": invoices,
            "erased_subjects": erasures,
        },
    }


def _zip_bytes(bundle: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "bundle_version": bundle["bundle_version"],
                    "generated_at": bundle["generated_at"],
                    "organization": bundle["organization"],
                    "retention_policy": bundle["retention_policy"],
                    "row_limit_per_section": bundle["row_limit_per_section"],
                    "section_counts": {
                        name: len(rows)
                        for name, rows in bundle["sections"].items()
                    },
                },
                indent=2,
            ),
        )
        for name, rows in bundle["sections"].items():
            archive.writestr(
                f"data/{name}.json",
                json.dumps(rows, indent=2, default=str),
            )
    return buffer.getvalue()


def request_export(
    db: Session,
    *,
    organization: Organization,
    requested_by_user_id: Optional[uuid.UUID],
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> ComplianceExport:
    record = ComplianceExport(
        organization_id=organization.id,
        requested_by_user_id=requested_by_user_id,
        status=EXPORT_PENDING,
        residency_region=residency_service.region_for_organization(organization),
    )
    db.add(record)
    db.flush()

    audit_service.record(
        db,
        organization_id=organization.id,
        actor_id=requested_by_user_id,
        resource_type=AuditResourceType.COMPLIANCE_EXPORT,
        resource_id=record.id,
        action=AuditAction.EXPORT_REQUESTED,
        details={"residency_region": record.residency_region},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return record


def generate_export(
    db: Session,
    *,
    organization: Organization,
    export: ComplianceExport,
) -> ComplianceExport:
    export.status = EXPORT_RUNNING
    db.add(export)
    db.flush()

    try:
        driver = residency_service.driver_for_organization(organization)
        key = residency_service.export_key(organization.id, export.id)
        payload = _zip_bytes(build_bundle(db, organization=organization))
        driver.put(key, payload, "application/zip")
    except Exception as exc:  # noqa: BLE001
        export.status = EXPORT_FAILED
        export.error_message = str(exc)[:500]
        export.completed_at = datetime.now(timezone.utc)
        db.add(export)
        db.flush()
        logger.exception(
            "compliance.export_failed",
            extra={
                "organization_id": str(organization.id),
                "export_id": str(export.id),
            },
        )
        return export

    now = datetime.now(timezone.utc)
    export.status = EXPORT_COMPLETE
    export.storage_key = key
    export.file_size_bytes = len(payload)
    export.completed_at = now
    export.expires_at = now + timedelta(hours=_ttl_hours())
    export.error_message = None
    db.add(export)
    db.flush()

    audit_service.record(
        db,
        organization_id=organization.id,
        actor_id=export.requested_by_user_id,
        resource_type=AuditResourceType.COMPLIANCE_EXPORT,
        resource_id=export.id,
        action=AuditAction.EXPORT_COMPLETED,
        details={
            "file_size_bytes": export.file_size_bytes,
            "residency_region": export.residency_region,
        },
    )

    logger.info(
        "compliance.export_complete",
        extra={
            "organization_id": str(organization.id),
            "export_id": str(export.id),
            "bytes": export.file_size_bytes,
        },
    )
    return export


def download_url_for(
    organization: Organization,
    export: ComplianceExport,
) -> str:
    if not export.is_downloadable:
        raise ExportNotReadyError(
            f"Export {export.id} is {export.status} and has no archive to serve."
        )
    if export.expires_at is not None and export.expires_at <= datetime.now(
        timezone.utc
    ):
        raise ExportNotReadyError(f"Export {export.id} has expired.")

    driver = residency_service.driver_for_region(export.residency_region)
    url = driver.presigned_get_url(
        export.storage_key or "", expires_in=DOWNLOAD_URL_TTL_SECONDS
    )
    if not url:
        raise ExportNotReadyError(
            "The storage backend for this region cannot mint presigned URLs."
        )
    return url


def list_exports(
    db: Session,
    *,
    organization_id: uuid.UUID,
    limit: int = 50,
) -> list[ComplianceExport]:
    return list(
        db.execute(
            select(ComplianceExport)
            .where(ComplianceExport.organization_id == organization_id)
            .order_by(ComplianceExport.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        .scalars()
        .all()
    )


def expire_stale_exports(
    db: Session,
    *,
    organization_id: Optional[uuid.UUID] = None,
    apply: bool = False,
) -> int:
    now = datetime.now(timezone.utc)
    query = select(ComplianceExport).where(
        ComplianceExport.status == EXPORT_COMPLETE,
        ComplianceExport.expires_at.isnot(None),
        ComplianceExport.expires_at <= now,
    )
    if organization_id is not None:
        query = query.where(ComplianceExport.organization_id == organization_id)

    stale = list(db.execute(query).scalars().all())
    if not apply:
        return len(stale)

    for record in stale:
        if record.storage_key:
            try:
                driver = residency_service.driver_for_region(record.residency_region)
                driver.delete(record.storage_key)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "compliance.export_bytes_not_reclaimed",
                    extra={"export_id": str(record.id)},
                )
        record.status = EXPORT_EXPIRED
        record.storage_key = None
        db.add(record)

    db.flush()
    return len(stale)
