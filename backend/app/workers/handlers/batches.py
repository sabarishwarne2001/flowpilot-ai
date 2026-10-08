"""The batch engine's two jobs (LIGHT profile: zip and hash work, no PDF engine, no models).

`batches.build_export_package`  builds one export package (app/services/batches/packages.py) and
                                tells the person who asked for it that it is ready, or why not.
`batches.sweep_export_packages` hourly: deletes the archives of packages past their expiry.

Idempotent: a package that is already READY, FAILED or EXPIRED is left as it is.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("app.workers.handlers.batches")

JOB_BUILD = "batches.build_export_package"
JOB_SWEEP = "batches.sweep_export_packages"


def _notify(db: Any, package: Any) -> None:
    from app.models.notification import (
        Notification,
        NotificationChannel,
        NotificationPriority,
        NotificationStatus,
        NotificationType,
    )

    if package.created_by_user_id is None:
        return
    ready = package.status == "READY"
    db.add(
        Notification(
            title="Export package ready" if ready else "Export package failed",
            message=(
                f"{package.name}: {package.document_count} documents, {package.file_count} files, "
                f"SHA-256 manifest {package.manifest_sha256[:12]}…"
                if ready
                else f"{package.name}: {package.error_detail or package.error_code}"
            ),
            notification_type=NotificationType.DOCUMENT,
            priority=NotificationPriority.SUCCESS if ready else NotificationPriority.ERROR,
            delivery_channel=NotificationChannel.IN_APP,
            delivery_status=NotificationStatus.SENT,
            workspace_id=package.workspace_id,
            organization_id=package.organization_id,
            user_id=package.created_by_user_id,
            is_read=False,
        )
    )


def handle_build_export_package(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
    from app.models.batches import ExportPackage
    from app.services import audit_service
    from app.services.batches import packages

    raw = payload.get("package_id")
    if not raw:
        raise ValueError(f"{JOB_BUILD} requires package_id")
    with SessionLocal() as db:
        package = db.get(ExportPackage, uuid.UUID(str(raw)), with_for_update=True)
        if package is None:
            return {"built": False, "reason": "package no longer exists"}
        if package.status not in ("QUEUED", "BUILDING"):
            return {"built": False, "reason": f"package is {package.status}"}
        try:
            packages.build(db, package=package)
        except Exception as exc:  # noqa: BLE001 - storage or zip failure: the package says so
            logger.exception("batches.package_build_failed", extra={"package_id": str(raw)})
            db.rollback()
            package = db.get(ExportPackage, uuid.UUID(str(raw)))
            if package is None:
                return {"built": False, "reason": "package no longer exists"}
            packages.fail(package, "BUILD_ERROR", f"The package could not be built: {exc}")
        audit_service.record(
            db,
            organization_id=package.organization_id,
            workspace_id=package.workspace_id,
            actor_id=package.created_by_user_id,
            resource_type=AuditResourceType.EXPORT_PACKAGE,
            resource_id=package.id,
            action=AuditAction.EXPORT_COMPLETED,
            outcome=AuditOutcome.ALLOWED if package.status == "READY" else AuditOutcome.DENIED,
            details={
                "status": package.status,
                "documents": package.document_count,
                "files": package.file_count,
                "bytes": package.size_bytes,
                "package_sha256": package.package_sha256,
                "manifest_sha256": package.manifest_sha256,
                "error_code": package.error_code,
            },
        )
        _notify(db, package)
        db.commit()
        result = {
            "built": package.status == "READY",
            "status": package.status,
            "files": package.file_count,
            "bytes": package.size_bytes,
            "package_sha256": package.package_sha256,
        }
    logger.info("batches.package_built", extra={"package_id": str(raw), **result})
    return result


def handle_sweep_export_packages(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.services.batches import packages

    with SessionLocal() as db:
        expired = packages.expire(db)
        db.commit()
    return {"expired": expired}


__all__ = ["JOB_BUILD", "JOB_SWEEP", "handle_build_export_package", "handle_sweep_export_packages"]
