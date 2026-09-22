"""ARCH-32 — the impure half. Sessions, storage, metering, audit.

EVERYTHING THAT TOUCHES THE DATABASE IS HERE
============================================

`detect.py`, `rasterize.py`, `assemble.py`, `leakcheck.py`, `validators.py`
and `manifest.py` are pure by contract. This module is the only one that
holds a `Session`, reads a clock, or talks to MinIO — which is what makes the
pure modules testable offline and what keeps the one dangerous value in this
phase, the matched plaintext, inside a single function's local scope.

THE PLAINTEXT LIFECYCLE, STATED ONCE
====================================

    run_detection:  candidates carry text -> token_digest(text) -> row
                    candidates dropped when the function returns

    run_apply:      detection re-run in memory -> text used for the leak
                    check -> discarded when the function returns

Between those two calls, the plaintext does not exist anywhere in this
system. That is why the apply job re-detects rather than reading what detect
found: reading it back would require having stored it.

The cost is one extra detection pass per apply, over text that is already in
`document_chunks`. That is a few hundred milliseconds against a render at
300 DPI, and it buys the property the whole phase is sold on.

WHY APPLY RE-DERIVES BUT STILL USES THE STORED REGIONS FOR GEOMETRY
===================================================================

The BOXES come from `redaction_regions`, because the reviewer toggled and
drew them and their decisions are the authority. The SECRETS come from the
re-run detection, because only the leak check needs them and only it gets
them. The two are matched by digest, not by position: a region whose digest
no longer appears in the re-derived set is still burned (the reviewer wanted
it burned) but contributes no secret to the leak check, and that mismatch is
recorded in the manifest as `regions_without_current_match`.
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import StorageNamespace, get_storage_driver, tenant_key
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.redaction import RedactionJob, RedactionRegion
from app.models.uploaded_file import UploadedFile
from app.models.work_item import WorkItem
from app.services import audit_service, job_service
from app.services.document_models import pages_from_work_item
from app.services.redaction import detect as detect_module
from app.services.redaction import manifest as manifest_module
from app.services.redaction import token_digest as digest_module
from app.services.redaction.vocabulary import (
    DEFAULT_RENDER_DPI,
    DETECTOR_MANUAL,
    JOB_STATUS_APPLYING,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_DETECTING,
    JOB_STATUS_FAILED,
    JOB_STATUS_REVIEW,
    MAX_RENDER_DPI,
    MIN_RENDER_DPI,
    PRECISION_MANUAL,
    profile_detectors,
    profile_mentions_names,
)

logger = logging.getLogger("app.services.redaction.service")

DETECT_JOB_TYPE = "redaction.detect"
APPLY_JOB_TYPE = "redaction.apply"
USAGE_EVENT_TYPE = "redaction.page"

__all__ = [
    "DETECT_JOB_TYPE",
    "APPLY_JOB_TYPE",
    "USAGE_EVENT_TYPE",
    "RedactionError",
    "JobNotFound",
    "InvalidJobState",
    "start_job",
    "run_detection",
    "run_apply",
    "add_manual_region",
    "set_region_enabled",
    "approve_and_enqueue_apply",
    "render_source_page_png",
    "bundle_urls",
    "load_job",
]


class RedactionError(RuntimeError):
    """Never carries document content."""


class JobNotFound(RedactionError):
    pass


class InvalidJobState(RedactionError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_job(
    db: Session, *, job_id: uuid.UUID, workspace_id: uuid.UUID
) -> RedactionJob:
    """ARCH-02: the workspace predicate is in the query, not in a later check."""
    job = db.execute(
        select(RedactionJob).where(
            RedactionJob.id == job_id,
            RedactionJob.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if job is None:
        raise JobNotFound("Redaction job not found.")
    return job


def _resolve_source_key(db: Session, job: RedactionJob) -> str:
    file_row = db.get(UploadedFile, job.source_file_id)
    if file_row is None or not file_row.file_path:
        raise RedactionError("The source file for this job is no longer available.")
    return file_row.file_path


def _load_source(db: Session, job: RedactionJob) -> bytes:
    key = _resolve_source_key(db, job)
    data = get_storage_driver().get(key)
    actual = hashlib.sha256(data).hexdigest()
    if actual != job.source_sha256:
        # Not a warning. The regions were computed against a specific set of
        # bytes; burning them into different bytes places rectangles by
        # coordinate onto a document nobody reviewed.
        raise RedactionError(
            "The source document has changed since this job was created. "
            "Start a new redaction job so the regions are reviewed against "
            "the current file."
        )
    return data


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


def start_job(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    work_item_id: uuid.UUID,
    user_id: uuid.UUID,
    profile_key: str,
    render_dpi: int = DEFAULT_RENDER_DPI,
    restore_text_layer: bool = True,
) -> RedactionJob:
    """Create a DETECTING job and enqueue `redaction.detect`."""
    profile_detectors(profile_key)  # raises UnknownProfileError on a bad key

    if not MIN_RENDER_DPI <= render_dpi <= MAX_RENDER_DPI:
        raise InvalidJobState(
            f"render_dpi must be between {MIN_RENDER_DPI} and {MAX_RENDER_DPI}."
        )

    work_item = db.execute(
        select(WorkItem).where(
            WorkItem.id == work_item_id, WorkItem.workspace_id == workspace_id
        )
    ).scalar_one_or_none()
    if work_item is None:
        raise JobNotFound("Work item not found.")

    source_file_id = getattr(work_item, "uploaded_file_id", None) or getattr(
        work_item, "file_id", None
    )
    if source_file_id is None:
        raise InvalidJobState("This work item has no stored source file.")

    file_row = db.get(UploadedFile, source_file_id)
    if file_row is None:
        raise InvalidJobState("This work item's source file is no longer available.")
    if (file_row.mime_type or "").lower() != "application/pdf":
        raise InvalidJobState(
            "Redaction rebuilds a PDF from its own rendered pages. "
            f"This work item is {file_row.mime_type or 'of unknown type'}."
        )

    job = RedactionJob(
        organization_id=organization_id,
        workspace_id=workspace_id,
        work_item_id=work_item_id,
        source_file_id=file_row.id,
        source_sha256=file_row.checksum_sha256,
        status=JOB_STATUS_DETECTING,
        profile_key=profile_key,
        render_dpi=render_dpi,
        restore_text_layer=restore_text_layer,
        created_by_user_id=user_id,
    )
    db.add(job)
    db.flush([job])

    job_service.enqueue(
        db,
        job_type=DETECT_JOB_TYPE,
        payload={
            "redaction_job_id": str(job.id),
            "organization_id": str(organization_id),
            "workspace_id": str(workspace_id),
        },
        organization_id=organization_id,
        idempotency_key=f"{DETECT_JOB_TYPE}:{job.id}",
    )

    audit_service.record(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        resource_type=AuditResourceType.UPLOADED_FILE,
        resource_id=work_item_id,
        action=AuditAction.CREATED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "redaction_job_id": str(job.id),
            "profile": profile_key,
            "render_dpi": render_dpi,
            "names_limit_applies": profile_mentions_names(profile_key),
        },
    )
    return job


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _geometries(
    db: Session, job: RedactionJob, work_item: WorkItem
) -> list[detect_module.PageGeometry]:
    """Pixel dimensions from extraction, point dimensions from the PDF itself.

    Both are needed and neither can substitute for the other: the OCR boxes
    are in pixels of whatever raster the extractor used, and regions are
    stored in the PDF's own point space.
    """
    from app.services.redaction.rasterize import page_dimensions

    data = _load_source(db, job)
    points = {n: (w, h) for n, w, h in page_dimensions(data)}

    entries = (work_item.extraction_metadata or {}).get("pages") or []
    pixels: dict[int, tuple[Optional[int], Optional[int]]] = {}
    for index, entry in enumerate(entries):
        number = int(entry.get("page_number") or index + 1)
        pixels[number] = (entry.get("width"), entry.get("height"))

    geometries: list[detect_module.PageGeometry] = []
    for number, (width_pt, height_pt) in sorted(points.items()):
        width_px, height_px = pixels.get(number, (None, None))
        geometries.append(
            detect_module.PageGeometry(
                page_number=number,
                width_points=width_pt,
                height_points=height_pt,
                width_px=int(width_px) if width_px else None,
                height_px=int(height_px) if height_px else None,
            )
        )
    return geometries


def _known_parties(work_item: WorkItem) -> list[str]:
    """Names the document's own extraction already found. §3.9's whole scope."""
    entities = work_item.extracted_entities or {}
    names: list[str] = []
    for key in ("parties", "signatories", "patients", "counterparties", "names"):
        value = entities.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    names.append(item)
                elif isinstance(item, dict):
                    candidate = item.get("name") or item.get("value")
                    if isinstance(candidate, str):
                        names.append(candidate)
        elif isinstance(value, str):
            names.append(value)
    return [n for n in {n.strip() for n in names} if len(n.strip()) >= 3]


def _detect_in_memory(
    db: Session, job: RedactionJob
) -> tuple[detect_module.DetectionResult, WorkItem]:
    work_item = db.get(WorkItem, job.work_item_id)
    if work_item is None:
        raise JobNotFound("Work item not found.")

    pages = pages_from_work_item(
        work_item.extraction_metadata, work_item.extracted_text
    )
    result = detect_module.detect_candidates(
        pages=pages,
        geometries=_geometries(db, job, work_item),
        profile_key=job.profile_key,
        known_parties=_known_parties(work_item),
    )
    return result, work_item


def run_detection(db: Session, *, job_id: uuid.UUID) -> dict[str, Any]:
    """Write one region per candidate, then move the job to REVIEW."""
    job = db.get(RedactionJob, job_id)
    if job is None:
        raise JobNotFound("Redaction job not found.")
    if job.status != JOB_STATUS_DETECTING:
        return {"outcome": "SKIPPED", "reason": f"job is {job.status}"}

    result, work_item = _detect_in_memory(db, job)
    generation = digest_module.key_generation()

    for candidate in result.candidates:
        db.add(
            RedactionRegion(
                job_id=job.id,
                organization_id=job.organization_id,
                workspace_id=job.workspace_id,
                page_number=candidate.page_number,
                x0=Decimal(f"{candidate.x0:.4f}"),
                y0=Decimal(f"{candidate.y0:.4f}"),
                x1=Decimal(f"{candidate.x1:.4f}"),
                y1=Decimal(f"{candidate.y1:.4f}"),
                detector=candidate.detector,
                confidence=Decimal(f"{candidate.confidence:.4f}"),
                geometry_precision=candidate.geometry_precision,
                enabled=True,
                # The ONLY thing derived from the plaintext that survives
                # this loop. `candidate.text` goes out of scope with
                # `result` when this function returns.
                token_digest=digest_module.token_digest(candidate.text),
            )
        )

    job.page_count = work_item.page_count
    job.status = JOB_STATUS_REVIEW
    db.flush()

    audit_service.record(
        db,
        organization_id=job.organization_id,
        workspace_id=job.workspace_id,
        resource_type=AuditResourceType.UPLOADED_FILE,
        resource_id=job.work_item_id,
        action=AuditAction.UPDATED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "redaction_job_id": str(job.id),
            "regions": len(result.candidates),
            "by_detector": result.by_detector(),
            "unplaced": result.unplaced,
            "pages_without_geometry": result.pages_without_geometry,
            "token_digest_key_generation": generation,
        },
    )
    return {
        "outcome": "COMPLETED",
        "regions": len(result.candidates),
        "unplaced": result.unplaced,
    }


# ---------------------------------------------------------------------------
# Review actions
# ---------------------------------------------------------------------------


def set_region_enabled(
    db: Session,
    *,
    job: RedactionJob,
    region_id: uuid.UUID,
    enabled: bool,
    user_id: uuid.UUID,
) -> RedactionRegion:
    if job.status != JOB_STATUS_REVIEW:
        raise InvalidJobState(
            f"Regions can only be changed while the job is in REVIEW; "
            f"this one is {job.status}."
        )
    region = db.execute(
        select(RedactionRegion).where(
            RedactionRegion.id == region_id, RedactionRegion.job_id == job.id
        )
    ).scalar_one_or_none()
    if region is None:
        raise JobNotFound("Region not found.")
    region.enabled = enabled
    region.toggled_by_user_id = user_id
    db.flush([region])
    return region


def add_manual_region(
    db: Session,
    *,
    job: RedactionJob,
    page_number: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    user_id: uuid.UUID,
) -> RedactionRegion:
    if job.status != JOB_STATUS_REVIEW:
        raise InvalidJobState(
            f"Regions can only be added while the job is in REVIEW; "
            f"this one is {job.status}."
        )
    if x1 <= x0 or y1 <= y0:
        raise InvalidJobState("A region must have positive width and height.")
    if page_number < 1:
        raise InvalidJobState("Page numbers start at 1.")

    region = RedactionRegion(
        job_id=job.id,
        organization_id=job.organization_id,
        workspace_id=job.workspace_id,
        page_number=page_number,
        x0=Decimal(f"{x0:.4f}"),
        y0=Decimal(f"{y0:.4f}"),
        x1=Decimal(f"{x1:.4f}"),
        y1=Decimal(f"{y1:.4f}"),
        detector=DETECTOR_MANUAL,
        confidence=Decimal("1.0000"),
        geometry_precision=PRECISION_MANUAL,
        enabled=True,
        # NULL, not a digest of "". There is no matched text for a rectangle
        # a person drew, and a constant here would read like evidence.
        token_digest=None,
        # `ck_rr_manual_has_author` refuses the row without this.
        created_by_user_id=user_id,
    )
    db.add(region)
    db.flush([region])
    return region


def approve_and_enqueue_apply(
    db: Session, *, job: RedactionJob, user_id: uuid.UUID
) -> RedactionJob:
    """Record the approval, move to APPLYING, enqueue the work."""
    if job.status != JOB_STATUS_REVIEW:
        raise InvalidJobState(
            f"Only a job in REVIEW can be applied; this one is {job.status}."
        )
    enabled = [r for r in job.regions if r.enabled]
    if not enabled:
        raise InvalidJobState(
            "Every region is switched off, so this would produce a document "
            "that looks redacted and is not. Enable at least one region or "
            "cancel the job."
        )

    job.approved_by_user_id = user_id
    job.approved_at = _now()
    job.status = JOB_STATUS_APPLYING
    db.flush([job])

    job_service.enqueue(
        db,
        job_type=APPLY_JOB_TYPE,
        payload={
            "redaction_job_id": str(job.id),
            "organization_id": str(job.organization_id),
            "workspace_id": str(job.workspace_id),
        },
        organization_id=job.organization_id,
        idempotency_key=f"{APPLY_JOB_TYPE}:{job.id}",
    )

    audit_service.record(
        db,
        organization_id=job.organization_id,
        workspace_id=job.workspace_id,
        resource_type=AuditResourceType.UPLOADED_FILE,
        resource_id=job.work_item_id,
        # HARDENING-T1:D32. Was AuditAction.APPROVED, which exists neither in
        # Python nor in the audit_action database enum, so approving a
        # redaction always raised and no redaction could ever be applied.
        # UPDATED with the decision in details needs no enum migration.
        action=AuditAction.UPDATED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "decision": "APPROVED",
            "redaction_job_id": str(job.id),
            "regions_enabled": len(enabled),
            "regions_disabled": len(job.regions) - len(enabled),
        },
    )
    return job


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def render_source_page_png(
    db: Session, *, job: RedactionJob, page_number: int, dpi: int, burn: bool
) -> bytes:
    """Render one page as PNG, optionally through the apply job's burn path.

    `burn=True` is what "Preview result" calls. It is the SAME
    `rasterize.burn_page` the apply job runs, with the same padding and the
    same uniform-fill verification — so a preview that looks right and an
    output that is wrong cannot both happen. A second, "fast" preview
    renderer would be a second answer to what the file will look like.
    """
    from app.services.redaction.rasterize import Box, burn_page, render_pages

    if not MIN_RENDER_DPI // 2 <= dpi <= MAX_RENDER_DPI:
        raise InvalidJobState(f"Preview dpi {dpi} is out of range.")

    data = _load_source(db, job)
    pages = render_pages(data, dpi=dpi, pages=[page_number], grayscale=False)
    if not pages:
        raise JobNotFound(f"Page {page_number} does not exist in this document.")
    page = pages[0]

    if burn:
        boxes = [
            Box(
                page_number=page_number,
                x0=float(region.x0),
                y0=float(region.y0),
                x1=float(region.x1),
                y1=float(region.y1),
            )
            for region in job.regions
            if region.enabled and region.page_number == page_number
        ]
        page = burn_page(page, boxes).page

    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(page.pixels).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _store(
    db: Session,
    *,
    job: RedactionJob,
    data: bytes,
    suffix: str,
    mime_type: str,
    filename: str,
) -> UploadedFile:
    file_id = uuid.uuid4()
    key = tenant_key(
        organization_id=job.organization_id,
        namespace=StorageNamespace.DERIVED,
        file_id=file_id,
        suffix=suffix,
    )
    get_storage_driver().put(key, data, mime_type)
    row = UploadedFile(
        id=file_id,
        owner_id=job.approved_by_user_id or job.created_by_user_id,
        organization_id=job.organization_id,
        workspace_id=job.workspace_id,
        file_path=key,
        original_filename=filename,
        mime_type=mime_type,
        file_size=len(data),
        checksum_sha256=hashlib.sha256(data).hexdigest(),
    )
    db.add(row)
    db.flush([row])
    return row


def run_apply(db: Session, *, job_id: uuid.UUID) -> dict[str, Any]:
    """render -> burn -> verify -> assemble -> leak check -> store -> seal.

    The order is not negotiable and the failures are not recoverable in
    place. Any exception below leaves the job FAILED with a reason, because
    the alternative — a retry that re-renders and re-uploads — would leave
    orphaned objects in MinIO under a job that may since have been cancelled.
    """
    from app.services.redaction.assemble import assemble_pdf
    from app.services.redaction.leakcheck import check_output
    from app.services.redaction.rasterize import Box, burn_page, render_pages

    job = db.get(RedactionJob, job_id)
    if job is None:
        raise JobNotFound("Redaction job not found.")
    if job.status != JOB_STATUS_APPLYING:
        return {"outcome": "SKIPPED", "reason": f"job is {job.status}"}

    try:
        source = _load_source(db, job)

        enabled = [r for r in job.regions if r.enabled]
        disabled = len(job.regions) - len(enabled)

        fingerprints = [
            manifest_module.RegionFingerprint(
                page_number=region.page_number,
                x0=region.x0,
                y0=region.y0,
                x1=region.x1,
                y1=region.y1,
                detector=region.detector,
                geometry_precision=region.geometry_precision,
            )
            for region in enabled
        ]
        job.input_digest = manifest_module.input_digest(
            source_sha256=job.source_sha256,
            profile_key=job.profile_key,
            render_dpi=job.render_dpi,
            grayscale=True,
            restore_text_layer=job.restore_text_layer,
            regions=fingerprints,
        )

        # ---- the secrets, for the length of this block only ---------------
        detection, _work_item = _detect_in_memory(db, job)
        digests = {
            digest_module.token_digest(c.text): c.text for c in detection.candidates
        }
        secrets = list(digests.values())
        unmatched = sum(
            1
            for region in enabled
            if region.token_digest and region.token_digest not in digests
        )

        # ---- render, burn, verify -----------------------------------------
        pages = render_pages(source, dpi=job.render_dpi, grayscale=True)
        burned = []
        for page in pages:
            boxes = [
                Box(
                    page_number=page.page_number,
                    x0=float(r.x0),
                    y0=float(r.y0),
                    x1=float(r.x1),
                    y1=float(r.y1),
                )
                for r in enabled
                if r.page_number == page.page_number
            ]
            burned.append(burn_page(page, boxes))

        document = assemble_pdf([b.page for b in burned])
        output_sha = hashlib.sha256(document.pdf_bytes).hexdigest()

        # ---- leak check on the OUTPUT --------------------------------------
        report = check_output(document.pdf_bytes, secrets)
        job.leak_check_passed = report.passed
        job.leak_check_detail = report.summary()

        if not report.passed:
            job.status = JOB_STATUS_FAILED
            job.failure_reason = report.sentence()
            db.flush([job])
            logger.error(
                "redaction.leak_check_failed",
                extra={"redaction_job_id": str(job.id), **report.summary()},
            )
            return {"outcome": "FAILED", "reason": "leak_check"}

        # ---- store ---------------------------------------------------------
        output_row = _store(
            db,
            job=job,
            data=document.pdf_bytes,
            suffix="pdf",
            mime_type="application/pdf",
            filename=f"redacted-{job.work_item_id}.pdf",
        )

        payload = manifest_module.build_manifest(
            job_id=str(job.id),
            organization_id=str(job.organization_id),
            workspace_id=str(job.workspace_id),
            work_item_id=str(job.work_item_id),
            source_sha256=job.source_sha256,
            output_sha256=output_sha,
            profile_key=job.profile_key,
            render_dpi=job.render_dpi,
            grayscale=True,
            restore_text_layer=job.restore_text_layer,
            text_layer_applied=False,
            page_count=document.page_count,
            output_bytes=len(document.pdf_bytes),
            regions=fingerprints,
            disabled_region_count=disabled,
            leak_check=report.summary(),
            approved_by=str(job.approved_by_user_id) if job.approved_by_user_id else None,
            approved_at=job.approved_at.isoformat() if job.approved_at else None,
            created_at=job.created_at.isoformat() if job.created_at else None,
            completed_at=_now().isoformat(),
            digest=job.input_digest,
        )
        payload["regions"]["without_current_match"] = unmatched
        manifest_bytes = manifest_module.canonical_json(payload)
        manifest_row = _store(
            db,
            job=job,
            data=manifest_bytes,
            suffix="json",
            mime_type="application/json",
            filename=f"redaction-manifest-{job.id}.json",
        )

        # ---- meter, then seal ----------------------------------------------
        _meter_pages(db, job=job, pages=document.page_count)

        job.output_file_id = output_row.id
        job.output_sha256 = output_sha
        job.manifest_file_id = manifest_row.id
        job.page_count = document.page_count
        job.status = JOB_STATUS_COMPLETED
        db.flush([job])

        # ARCH37-S1:redaction-completed
        from app.services import outbox_service

        outbox_service.emit_trigger(
            db,
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            event_type="trigger.redaction.completed",
            resource_id=job.work_item_id,
            payload={
                "work_item_id": str(job.work_item_id),
                "redaction_job_id": str(job.id),
                "profile": job.profile_key,
                "pages": document.page_count,
            },
            idempotency_key=f"trigger.redaction.completed:{job.id}",
        )

        audit_service.record(
            db,
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            resource_type=AuditResourceType.UPLOADED_FILE,
            resource_id=job.work_item_id,
            action=AuditAction.UPDATED,
            outcome=AuditOutcome.ALLOWED,
            details={
                "redaction_job_id": str(job.id),
                "output_sha256": output_sha,
                "pages": document.page_count,
                "regions_applied": len(enabled),
                "leak_check": report.summary(),
            },
        )
        return {
            "outcome": "COMPLETED",
            "pages": document.page_count,
            "output_sha256": output_sha,
        }

    except Exception as exc:  # noqa: BLE001
        # The message is the operator's only signal, and `ck_rj_failed_has_reason`
        # refuses the row without one. It carries the exception TYPE and a
        # fixed sentence, never the exception's own text: a traceback from
        # deep in a PDF library can quote page content.
        job.status = JOB_STATUS_FAILED
        job.failure_reason = (
            f"The redaction could not be completed ({type(exc).__name__}). "
            "The document was not changed and nothing was published."
        )
        db.flush([job])
        logger.exception(
            "redaction.apply_failed", extra={"redaction_job_id": str(job_id)}
        )
        return {"outcome": "FAILED", "reason": type(exc).__name__}


def _meter_pages(db: Session, *, job: RedactionJob, pages: int) -> None:
    """One `redaction.page` event per OUTPUT page, once per job.

    Idempotency is keyed on the redaction job id, not on the worker job id:
    a retried apply must not bill twice, and a re-applied job is a NEW
    redaction job with its own id, which must.
    """
    if pages <= 0:
        return
    from app.services import spend_control_service as spend

    with spend.guard_usage(
        db,
        organization_id=job.organization_id,
        event_type=USAGE_EVENT_TYPE,
        estimated_quantity=pages,
        estimated_cost_micros=0,
        workspace_id=job.workspace_id,
        resource_type="WORK_ITEM",
        resource_id=job.work_item_id,
        idempotency_key=f"redaction:{job.id}",
    ) as guard:
        guard.record(
            quantity=pages,
            cost_micros=0,
            provider="internal",
            details={
                "profile": job.profile_key,
                "render_dpi": job.render_dpi,
                "redaction_job_id": str(job.id),
            },
        )


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


def bundle_urls(db: Session, *, job: RedactionJob, expires_in: int = 900) -> dict[str, Any]:
    """Signed URLs for the sanitized PDF and the manifest.

    Refuses unless the job is sealed. A bundle endpoint that served a
    half-written output would hand out the one artifact this phase exists to
    make trustworthy, before anything had verified it.
    """
    if not job.is_sealed:
        raise InvalidJobState(
            "This job has no verified output yet. A download is only offered "
            "once the leak check has passed."
        )

    driver = get_storage_driver()
    output = db.get(UploadedFile, job.output_file_id)
    manifest_row = db.get(UploadedFile, job.manifest_file_id)
    if output is None or manifest_row is None:
        raise RedactionError("The output objects for this job are missing.")

    return {
        "document_url": driver.presigned_get_url(output.file_path, expires_in=expires_in),
        "manifest_url": driver.presigned_get_url(
            manifest_row.file_path, expires_in=expires_in
        ),
        "output_sha256": job.output_sha256,
        "expires_in": expires_in,
    }